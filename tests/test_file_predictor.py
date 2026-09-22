"""Unit tests for FilePredictor"""

from __future__ import annotations

import pytest

from dhompo.serving.file_predictor import FilePredictor, PredictionResult
from tests.conftest import make_history_df


class TestFilePredictor:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.predictor = FilePredictor()

    def test_load_models(self):
        """FilePredictor initializes and loads all 5 horizon models."""
        assert len(self.predictor._models) == 5
        for h in range(1, 6):
            assert h in self.predictor._models

    def test_predict_from_history(self, sample_history_df):
        result = self.predictor.predict_from_history(sample_history_df)
        assert isinstance(result, PredictionResult)
        for key in ("h1", "h2", "h3", "h4", "h5"):
            assert key in result.predictions
            assert isinstance(result.predictions[key], float)

    def test_predict_short_history_raises(self):
        df = make_history_df(n_rows=10)
        with pytest.raises(ValueError, match="minimal 24 baris"):
            self.predictor.predict_from_history(df)

    def test_predict_exact_24_rows(self):
        df = make_history_df(n_rows=24)
        result = self.predictor.predict_from_history(df)
        assert isinstance(result, PredictionResult)

    def test_predictions_are_finite(self, sample_history_df):
        """All predictions should be finite numbers (not NaN/inf)."""
        import math

        result = self.predictor.predict_from_history(sample_history_df)
        for key, val in result.predictions.items():
            assert math.isfinite(val), f"{key}={val} is not finite"
