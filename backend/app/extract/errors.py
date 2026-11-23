"""Errors raised while turning a tutorial into an Extraction."""


class ExtractionError(Exception):
    """The tutorial could not be fetched or parsed. The message is safe to show to the user."""
