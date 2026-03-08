class AppError(Exception):
    status_code = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ConflictError(AppError):
    status_code = 409


class NotFoundError(AppError):
    status_code = 404


class ValidationError(AppError):
    status_code = 400


class InfrastructureError(AppError):
    status_code = 503

