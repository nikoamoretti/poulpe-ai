class PostgresAdapter:
    """Placeholder database adapter.

    Real persistence should live behind repository or unit-of-work boundaries once
    the SQLAlchemy integration is wired in.
    """

    def connect(self) -> None:
        raise NotImplementedError("Postgres wiring is not implemented in the scaffold yet.")

