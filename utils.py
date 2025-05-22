def shorten_address(address: str) -> str:
    """Shorten a blockchain address for display (first 6 and last 4 chars)."""
    if not address or len(address) < 10:
        return address
    return f"{address[:6]}...{address[-4:]}"
