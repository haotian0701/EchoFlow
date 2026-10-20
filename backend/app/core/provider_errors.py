class ProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        ambiguous: bool = False,
        metadata: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.metadata = metadata or {}
