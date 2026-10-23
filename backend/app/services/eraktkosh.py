
    async def _fetch_parse(
        self,
        state_code: str,
        component_id: str,
        component_name: str,
    ) -> SourceResult:
        params = {
            "stateCode": state_code,
            "componentId": component_id,
        }
        headers = {
            "Accept": "application/json",
            "User-Agent": (
                "BloodSignal/1.0 "
                "(read-only hackathon availability search)"
            ),
        }

        remaining = self._retry_after_until - self._retry_after_clock()
        if remaining > 0:
            raise SourceRateLimited(
                f"e-RaktKosh retry window active; "
                f"wait about {int(remaining) + 1}s"
            )

        try:
            async with self._semaphore:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        self._timeout,
                        connect=3.0,
                    ),
                    transport=self._transport,
                    follow_redirects=False,
                ) as client:
                    response = await client.get(
                        self._base,
                        params=params,
                        headers=headers,
                    )

        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            log.warning(
                "eRaktKosh transport failure: "
                "type=%s detail=%s state_code=%s component_id=%s",
                type(exc).__name__,
                str(exc)[:300],
                state_code,
                component_id,
            )
            raise SourceUnavailable(type(exc).__name__) from exc

        except httpx.HTTPError as exc:
            log.warning(
                "eRaktKosh HTTP client failure: "
                "type=%s detail=%s state_code=%s component_id=%s",
                type(exc).__name__,
                str(exc)[:300],
                state_code,
                component_id,
            )
            raise SourceUnavailable(type(exc).__name__) from exc

        status = response.status_code
        content_type = response.headers.get("content-type", "")

        log.info(
            "eRaktKosh response: status=%s content_type=%s "
            "state_code=%s component_id=%s",
            status,
            content_type[:100],
            state_code,
            component_id,
        )

        if status in (401, 403) or 300 <= status < 400:
            log.warning(
                "eRaktKosh access restricted: status=%s",
                status,
            )
            raise AccessNotPermitted(
                f"e-RaktKosh returned HTTP {status}; "
                "automated access stopped"
            )

        if status == 429:
            retry_after = response.headers.get("Retry-After", "60")

            try:
                delay = max(1.0, min(900.0, float(retry_after)))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(
                            tzinfo=timezone.utc
                        )
                    delay = max(
                        1.0,
                        min(
                            900.0,
                            (
                                retry_at - datetime.now(timezone.utc)
                            ).total_seconds(),
                        ),
                    )
                except (TypeError, ValueError, OverflowError):
                    delay = 60.0

            self._retry_after_until = (
                self._retry_after_clock() + delay
            )
            log.warning(
                "eRaktKosh rate limited request; retry_after=%s",
                int(delay),
            )
            raise SourceRateLimited(
                f"e-RaktKosh rate limited the request; "
                f"retry after {int(delay)}s"
            )

        if status >= 500:
            log.warning(
                "eRaktKosh server error: status=%s",
                status,
            )
            raise SourceUnavailable(f"HTTP {status}")

        if status >= 400:
            log.warning(
                "eRaktKosh rejected request: status=%s",
                status,
            )
            raise SourceRequestRejected(
                f"e-RaktKosh returned HTTP {status}"
            )

        body_start = response.text.lstrip()[:40].casefold()

        if (
            "json" not in content_type.casefold()
            and not body_start.startswith(("[", "{"))
        ):
            log.warning(
                "eRaktKosh returned non-JSON response: "
                "content_type=%s body_prefix=%r",
                content_type[:100],
                body_start[:40],
            )
            raise SourceMalformed(
                "e-RaktKosh returned non-JSON content "
                "(possibly an access/interstitial page)"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            log.warning(
                "eRaktKosh JSON decoding failed: "
                "state_code=%s component_id=%s",
                state_code,
                component_id,
            )
            raise SourceMalformed(
                "e-RaktKosh response was not valid JSON"
            ) from exc

        if not isinstance(payload, list):
            log.warning(
                "eRaktKosh schema mismatch: expected list, got %s",
                type(payload).__name__,
            )

        result = self._parse(
            payload,
            state_code,
            component_id,
            component_name,
        )

        log.info(
            "eRaktKosh parsed successfully: "
            "hospitals=%s records=%s invalid_rows=%s "
            "state_code=%s component_id=%s",
            len(result.banks),
            len(result.records),
            result.invalid_rows,
            state_code,
            component_id,
        )

        return result
