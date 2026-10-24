from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
import redis.asyncio as redis
from google import genai
from google.genai import types

log = logging.getLogger("geocorrect_agent")

class GeoCorrectAgent:
    """
    An agentic geocoding correction and parsing layer backed by Redis cache,
    using Gemini 3.5 Flash-Lite with Google Search verification to resolve accurate hospital names.
    """
    def __init__(
        self, 
        redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379"), 
        ttl_seconds: int = 86400 * 7,
        gemini_api_key: str | None = None
    ):
        self._redis_url = redis_url
        self._ttl = ttl_seconds
        self._redis_client: redis.Redis | None = None
        
        api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")
        if api_key:
            self._client = genai.Client(api_key=api_key)
            self._model_name = "gemini-3.5-flash-lite"
        else:
            self._client = None
            self._model_name = None

    async def _get_redis(self) -> redis.Redis | None:
        if not self._redis_client and self._redis_url:
            try:
                self._redis_client = redis.from_url(self._redis_url, decode_responses=True)
            except Exception as exc:
                log.warning("Failed to initialize Redis for geocorrect agent: %s", exc)
        return self._redis_client

    def _rule_based_sanitize(self, name: str | None, address: str | None, district: str | None, state: str | None) -> str:
        """Fallback Tier: Fast rule-based heuristic parsing."""
        if not name:
            name = ""
        combined = f"{name} {address or ''}"
        
        cleaned = re.sub(r'\b(survey|plot|door|room|floor|block|phase|ward|near|opp|opposite|behind)\b.*?(?=,|\b[A-Z][a-z]+\b|$)', '', combined, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b\d+(st|nd|rd|th)?\s*(floor|block|phase|gate)\b', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\b(Prop|Pvt|Ltd|Limited|Healthcare|Trust|Society|Co-op|Dept of.*?and)\b', '', cleaned, flags=re.IGNORECASE)
        
        hospital_matches = re.findall(r'([A-Za-z\s]+(?:Hospital|Medical College|Institute|Blood Centre|Infirmary|Clinic))', combined, flags=re.IGNORECASE)
        if hospital_matches:
            core_name = max(hospital_matches, key=len).strip()
        else:
            core_name = re.sub(r'[,.\-_/&@#()]+', ' ', cleaned)
            core_name = " ".join(core_name.split()[:3])

        target_district = district or ""
        target_state = state or ""
        
        parts = [core_name]
        if target_district and target_district.casefold() not in core_name.casefold():
            parts.append(target_district)
        if target_state and target_state.casefold() not in core_name.casefold():
            parts.append(target_state)
            
        return re.sub(r'\s+', ' ', " ".join(parts)).strip()

    async def _gemini_search_verified_sanitize(self, raw_name: str, raw_address: str | None, district: str | None, state: str | None) -> str:
        """Primary Tier: Gemini 3.5 Flash-Lite with Google Search grounding to verify real-world hospital names."""
        if not self._client or not self._model_name:
            raise ValueError("Gemini client not initialized")
            
        prompt = f"""
Search the internet to verify and find the correct official public name and location for this Indian hospital/blood center.
Raw Name: {raw_name}
Raw Address: {raw_address or 'N/A'}
District: {district or 'N/A'}
State: {state or 'N/A'}

Instructions:
1. Use Google Search to cross-reference the actual hospital name and its city/district.
2. Return ONLY the verified official hospital name, followed by its city/district and state (e.g., "GBP Hospital Agartala Tripura").
3. Do not include extra conversational filler, quotes, or markdown.
"""
        # Enable Google Search tool grounding so the model can verify names online
        response = await self._client.aio.models.generate_content(
            model=self._model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}],
            ),
        )
        verified_query = response.text.strip().replace('"', '')
        if not verified_query:
            raise ValueError("Empty response from Gemini search model")
            
        return verified_query

    async def resolve_location(
        self,
        *,
        name: str,
        address: str | None,
        district: str | None,
        state: str | None,
        geocoder: Any
    ) -> tuple[float, float, str] | None:
        """
        Resolves location using: Redis cache -> Gemini with Google Search (Primary) -> Rule-based parser (Fallback) -> Geocoder.
        """
        clean_cache_name = re.sub(r'[^a-zA-Z0-9]', '_', name.casefold())
        clean_cache_dist = re.sub(r'[^a-zA-Z0-9]', '_', (district or '').casefold())
        cache_key = f"geocorrect:v4_search:{clean_cache_name}:{clean_cache_dist}"
        
        client = await self._get_redis()
        if client:
            try:
                cached_data = await client.get(cache_key)
                if cached_data:
                    data = json.loads(cached_data)
                    return data["lat"], data["lon"], data["precision"]
            except Exception as exc:
                log.debug("Redis cache read error: %s", exc)

        corrected_query = None
        
        # Step 1: Try Primary Gemini Search-Grounded Verification
        if self._client:
            try:
                corrected_query = await self._gemini_search_verified_sanitize(name, address, district, state)
            except Exception as exc:
                log.debug("Gemini search verification failed, falling back to rules: %s", exc)

        # Step 2: Fallback to Rule-Based Parsing if search/LLM fails
        if not corrected_query:
            corrected_query = self._rule_based_sanitize(name, address, district, state)

        # Step 3: Attempt Geocoding with the verified query
        found = None
        try:
            found = await geocoder.geocode(
                name=corrected_query,
                address=address,
                city=None,
                district=district,
                state=state,
                budget_s=2.5
            )
        except Exception as exc:
            log.warning("Geocoding lookup failed for query '%s': %s", corrected_query, exc)

        # Step 4: Write successfully resolved result to Redis cache
        if found and client:
            try:
                lat, lon, precision = found
                await client.setex(
                    cache_key,
                    self._ttl,
                    json.dumps({"lat": lat, "lon": lon, "precision": precision})
                )
            except Exception as exc:
                log.debug("Redis cache write error: %s", exc)

        return found
