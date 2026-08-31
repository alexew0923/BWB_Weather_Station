"""Structured domain errors.

Every failure this engine can produce is one of these types. They carry a
``code`` a future frontend can branch on, a ``summary`` that is safe to show a
person, and a ``detail`` that names the specific cause. Nothing in the public
API is allowed to escape as a bare pandas or urllib traceback.

The distinction that matters most is the one StationWatch already draws: a
failure to *observe* the source is never reported as a statement about the
environment.
"""


class EnvironmentalAnalysisError(Exception):
    """Base class for every failure raised by this engine."""

    code = "environmental_error"
    default_summary = "The environmental analysis engine could not complete."

    def __init__(self, detail, summary=None):
        super().__init__(detail)
        self.detail = detail
        self.summary = summary or self.default_summary

    def to_dict(self):
        """Serialise the error for a machine consumer."""
        return {"code": self.code, "summary": self.summary, "detail": self.detail}

    def __str__(self):
        return self.detail


class ConfigurationError(EnvironmentalAnalysisError):
    """A required setting is missing or unusable.

    Configuration faults are observation faults: they say nothing about the
    environment, so they must never be rendered as an environmental state.
    """

    code = "configuration_error"
    default_summary = (
        "The environmental analysis engine has no usable data source configured."
    )


class SourceUnavailableError(EnvironmentalAnalysisError):
    """The remote telemetry source could not be retrieved."""

    code = "source_unavailable"
    default_summary = "The telemetry source could not be retrieved."


class SourceFormatError(EnvironmentalAnalysisError):
    """The source responded, but not with usable CSV telemetry."""

    code = "source_format_error"
    default_summary = "The telemetry source did not return readable CSV telemetry."


class SchemaError(EnvironmentalAnalysisError):
    """The CSV is readable but does not carry the columns the engine needs."""

    code = "schema_error"
    default_summary = "The telemetry source is missing columns the analysis requires."


class EmptyDatasetError(EnvironmentalAnalysisError):
    """The source was read successfully and holds no usable observations.

    This is not a source failure and not an environmental statement. It is the
    honest answer that there is nothing to analyse.
    """

    code = "empty_dataset"
    default_summary = "The telemetry source holds no usable observations."


class InsufficientDataError(EnvironmentalAnalysisError):
    """There is data, but not enough of it to answer the question asked."""

    code = "insufficient_data"
    default_summary = "There is not enough valid telemetry to support this analysis."


class UnknownEventError(EnvironmentalAnalysisError):
    """A caller asked for an event id this analysis did not produce."""

    code = "unknown_event"
    default_summary = "No such environmental event exists in this analysis."
