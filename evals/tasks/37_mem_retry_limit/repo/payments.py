MAX_RETRIES = 3


class ChargeError(Exception):
    pass


def charge_with_retries(charge):
    """Call charge() until it succeeds; after MAX_RETRIES failed attempts, re-raise the last error."""
    for attempt in range(MAX_RETRIES):
        try:
            return charge()
        except ChargeError:
            if attempt == MAX_RETRIES - 1:
                raise
