"""
FIXED: Agentic geocoding correction with robust fallback chain.
- Gemini with Google Search (optional, graceful degradation)
- Improved rule-based parsing with location hints
- Proper async/await for Redis
- Comprehensive error logging
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
import redis.asyncio as redis

log = logging.getLogger("geocorrect_agent")

# Optional: Only import genai if available
try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False
    genai = None
    types = None


class GeoCorrectAgent:
    """
    Multi-tier geocoding correction with robust fallback chain:
    1. Redis cache (fastest)
    2. Gemini with Google Search (optional, LLM-powered)
    3. Improved rule-based parsing (always available)
    4. Geocoder with validated input
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
        
        # Initialize Gemini only if API key is available and library is installed
        api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")
        self._client = None
        self._model_name = None
        
        if HAS_GENAI and api_key:
            try:
                self._client = genai.Client(api_key=api_key)
                self._model_name = "gemini-3.5-flash-lite"
                log.info("Gemini client initialized for geocorrect agent")
            except Exception as exc:
                log.warning("Failed to initialize Gemini client: %s", exc)
        elif not HAS_GENAI:
            log.debug("genai library not installed; LLM tier disabled")
        elif not api_key:
            log.debug("No Gemini API key provided; LLM tier disabled")

    async def _get_redis(self) -> redis.Redis | None:
        """Lazily initialize Redis connection."""
        if not self._redis_client and self._redis_url:
            try:
                self._redis_client = redis.from_url(self._redis_url, decode_responses=True)
                # Test the connection
                await self._redis_client.ping()
                log.debug("Redis connection established for geocorrect agent")
            except Exception as exc:
                log.warning("Failed to initialize Redis for geocorrect agent: %s", exc)
                self._redis_client = None
        return self._redis_client

    def _sanitize_hospital_name(self, name: str | None) -> str:
        """Extract core hospital name from messy input."""
        if not name:
            return ""
        
        # Remove common institutional suffixes
        cleaned = re.sub(
            r'\b(survey|plot|door|room|floor|block|phase|ward|near|opp|opposite|behind|'
            r'prop|pvt|ltd|limited|healthcare|trust|society|co-op|dept)\b.*$',
            '', name, flags=re.IGNORECASE
        )
        
        # Try to extract hospital/medical facility name patterns
        hospital_patterns = [
            r'([A-Za-z\s]+(?:Hospital|Medical\s+College|Institute|Blood\s+Centre|'
            r'Infirmary|Clinic|Nursing\s+Home|Care\s+Center))',
            r'([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)',  # Title-cased words
        ]
        
        for pattern in hospital_patterns:
            match = re.search(pattern, cleaned, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        # Fallback: take first 2-3 significant words
        words = re.findall(r'\b[A-Za-z]+(?:\s+[A-Za-z]+)*', cleaned)
        if words:
            return ' '.join(words[:2]).strip()
        
        return cleaned.strip()

    def _build_nominatim_query(
        self,
        hospital_name: str,
        address: str | None,
        district: str | None,
        state: str | None
    ) -> str:
        """
        Build a well-structured Nominatim query from components.
        Format: "Hospital Name, District, State" (proven to work with OSM)
        """
        parts = []
        
        # Add sanitized hospital name
        if hospital_name:
            parts.append(hospital_name)
        
        # Add district if available
        if district and district.strip():
            district_clean = district.strip().title()
            if district_clean not in hospital_name:
                parts.append(district_clean)
        
        # Add state if available
        if state and state.strip():
            state_clean = state.strip().title()
            if state_clean not in hospital_name and state_clean not in (district or ""):
                parts.append(state_clean)
        
        # Build final query
        query = ", ".join(parts)
        # Clean up any double spaces
        query = re.sub(r'\s+', ' ', query).strip()
        
        log.debug(f"Built Nominatim query: '{query}'")
        return query

    def _rule_based_sanitize(
        self, 
        name: str | None, 
        address: str | None, 
        district: str | None, 
        state: str | None
    ) -> str:
        """
        IMPROVED Tier 3: Rule-based parsing with proper Nominatim formatting.
        Returns a query optimized for OpenStreetMap's Nominatim service.
        """
        hospital_name = self._sanitize_hospital_name(name or address)
        query = self._build_nominatim_query(hospital_name, address, district, state)
        
        if not query:
            # Ultimate fallback: use whatever we have
            parts = [name, district, state]
            query = ", ".join(p for p in parts if p)
        
        log.info(f"Rule-based fallback query: '{query}'")
        return query

    async def _gemini_search_verified_sanitize(
        self, 
        raw_name: str, 
        raw_address: str | None, 
        district: str | None, 
        state: str | None
    ) -> str:
        """
        TIER 2 (Optional): Gemini 3.5 Flash-Lite with Google Search grounding.
        Returns verified hospital name for better geocoding.
        """
        if not self._client or not self._model_name:
            raise ValueError("Gemini client not initialized")
        
        prompt = f"""You are a geographic data verification expert for blood banks in India.
        
Task: Find the exact official name of this hospital/blood center and format it for map search.

Input:
- Raw Name: {raw_name}
- Raw Address: {raw_address or 'N/A'}
- District: {district or 'N/A'}
- State: {state or 'N/A'}

Instructions:
1. Search online for the correct official hospital name
2. Verify the district and state are correct
3. Return ONLY: "Hospital Name, District, State" (e.g., "GBP Hospital, West Tripura, Tripura")
4. Do not add explanations, quotes, or extra text
5. If the hospital cannot be verified, return the best available information"""

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[{"google_search": {}}],
                    max_output_tokens=200,
                    temperature=0.3,  # Low temperature for factual accuracy
                ),
            )
            verified_query = response.text.strip().replace('"', '').strip()
            if verified_query:
                log.info(f"Gemini verified query: '{verified_query}'")
                return verified_query
            else:
                raise ValueError("Empty response from Gemini")
        except Exception as exc:
            log.warning(f"Gemini search verification failed: {exc}")
            raise

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
        FIXED Multi-tier location resolution:
        1. Redis cache -> Instant return
        2. Gemini Search (if available) -> Best accuracy
        3. Rule-based parsing -> Always works
        4. Nominatim geocoding -> Gets coordinates
        5. Cache result for future queries
        
        Returns: (latitude, longitude, precision) or None if all tiers fail
        """
        # Build cache key
        clean_name = re.sub(r'[^a-zA-Z0-9]', '_', name.casefold())
        clean_dist = re.sub(r'[^a-zA-Z0-9]', '_', (district or '').casefold())
        cache_key = f"geocorrect:v5_fixed:{clean_name}:{clean_dist}"
        
        # TIER 1: Check Redis cache
        redis_client = await self._get_redis()
        if redis_client:
            try:
                cached_data = await redis_client.get(cache_key)
                if cached_data:
                    try:
                        data = json.loads(cached_data)
                        lat, lon, precision = data["lat"], data["lon"], data["precision"]
                        log.info(f"Cache hit for {name}: ({lat}, {lon})")
                        return lat, lon, precision
                    except (json.JSONDecodeError, KeyError, TypeError) as exc:
                        log.debug(f"Corrupt cache entry: {exc}")
            except Exception as exc:
                log.debug(f"Redis read error: {exc}")
        
        corrected_query = None
        
        # TIER 2: Try Gemini Search Verification (if available)
        if self._client and self._model_name:
            try:
                corrected_query = await self._gemini_search_verified_sanitize(
                    name, address, district, state
                )
                log.info(f"Using Gemini-verified query: {corrected_query}")
            except Exception as exc:
                log.debug(f"Gemini search failed, using rule-based fallback: {exc}")
                corrected_query = None
        
        # TIER 3: Fallback to Improved Rule-Based Parsing
        if not corrected_query:
            corrected_query = self._rule_based_sanitize(name, address, district, state)
            log.info(f"Using rule-based query: {corrected_query}")
        
        # TIER 4: Attempt Nominatim Geocoding
        found = None
        try:
            if geocoder and hasattr(geocoder, 'geocode'):
                found = await geocoder.geocode(
                    name=corrected_query,
                    address=address,
                    city=None,
                    district=district,
                    state=state,
                    budget_s=3.0
                )
                if found:
                    lat, lon, precision = found
                    log.info(f"Geocoding successful: {corrected_query} -> ({lat}, {lon}, {precision})")
                else:
                    log.warning(f"Geocoding returned None for: {corrected_query}")
            else:
                log.error("Geocoder not available or not callable")
        except Exception as exc:
            log.error(f"Geocoding error for '{corrected_query}': {exc}", exc_info=True)
        
        # TIER 5: Cache successful result
        if found and redis_client:
            try:
                lat, lon, precision = found
                cache_data = json.dumps({"lat": lat, "lon": lon, "precision": precision})
                await redis_client.setex(cache_key, self._ttl, cache_data)
                log.debug(f"Cached result for {name}")
            except Exception as exc:
                log.debug(f"Redis write error: {exc}")
        
        return found
