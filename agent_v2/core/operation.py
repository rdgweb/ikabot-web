"""A managed agent never starts another game request without Hub authorization."""
class OperationBlocked(RuntimeError):
    pass
