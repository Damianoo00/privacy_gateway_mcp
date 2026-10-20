"""Shared PII detector and public error for the MCP proxy."""
from __future__ import annotations

import importlib.util


class GatewayError(Exception):
    """Public, non-confidential operation failure."""


class PresidioDetector:
    def __init__(self, language: str, spacy_model: str | None = None):
        try:
            if not spacy_model or importlib.util.find_spec(spacy_model) is None:
                raise RuntimeError("Local spaCy model unavailable")
            import spacy
            spacy.load(spacy_model)  # Local-only preflight before Presidio can attempt auto-download.
            from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
            from presidio_analyzer.nlp_engine import NlpEngineProvider
            provider = NlpEngineProvider(nlp_configuration={"nlp_engine_name": "spacy", "models": [
                {"lang_code": language, "model_name": spacy_model}]})
            self.engine = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=[language])
            # Force model loading at startup. No silent regex-only fallback.
            self.engine.nlp_engine.load()
            if language == "pl":
                patterns = {"PESEL": r"\b\d{11}\b", "NIP": r"\b\d{10}\b", "REGON": r"\b(?:\d{9}|\d{14})\b"}
                for entity, regex in patterns.items():
                    self.engine.registry.add_recognizer(PatternRecognizer(supported_entity=entity,
                        patterns=[Pattern(name=entity, regex=regex, score=0.5)], supported_language="pl"))
        except Exception as exc:
            raise RuntimeError("Presidio NLP model unavailable") from exc

    def detect(self, text: str, language: str, entities: tuple[str, ...], threshold: float) -> list[tuple[int, int, str]]:
        try:
            matches = self.engine.analyze(text=text, language=language, entities=list(entities), score_threshold=threshold)
            return [(m.start, m.end, m.entity_type) for m in matches]
        except Exception as exc:
            raise GatewayError("PII detection unavailable") from exc
