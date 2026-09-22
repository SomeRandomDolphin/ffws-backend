from __future__ import annotations

from sklearn.linear_model import Lasso, LinearRegression, Ridge, ElasticNet
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from xgboost import XGBRegressor

try:
    from lightgbm import LGBMRegressor
    _HAS_LIGHTGBM = True
except ImportError:
    _HAS_LIGHTGBM = False

try:
    from catboost import CatBoostRegressor
    _HAS_CATBOOST = True
except ImportError:
    _HAS_CATBOOST = False

HORIZONS: list[int] = [1, 2, 3, 4, 5]
HORIZON_STEPS: dict[int, int] = {h: h * 2 for h in HORIZONS}  # 30-min steps


def get_model_definitions(params: dict | None = None) -> dict[str, tuple]:
    """Return model definitions as {name: (estimator, use_scaled)}.

    Parameters
    ----------
    params:
        Optional hyperparameter overrides loaded from configs/shared/sklearn_model.yaml.
        Keys should match model names below.

    Returns
    -------
    dict
        Mapping from model name to (sklearn estimator, bool use_scaled).
        use_scaled=True → StandardScaler applied; False → raw features.
    """
    if params is None:
        params = {}

    rf_params = params.get("random_forest", {})
    gb_params = params.get("gradient_boosting", {})
    xgb_params = params.get("xgboost", {})
    lgbm_params = params.get("lightgbm", {})
    catboost_params = params.get("catboost", {})
    ridge_params = params.get("ridge", {})
    lasso_params = params.get("lasso", {})
    elasticnet_params = params.get("elasticnet", {})

    models = {
        "Linear Regression": (LinearRegression(), True),
        "Ridge": (
            Ridge(alpha=ridge_params.get("alpha", 1.0)),
            True,
        ),
        "Lasso": (
            Lasso(
                alpha=lasso_params.get("alpha", 0.01),
                max_iter=lasso_params.get("max_iter", 10000),
            ),
            True,
        ),
        "ElasticNet": (
            ElasticNet(
                alpha=elasticnet_params.get("alpha", 0.01),
                l1_ratio=elasticnet_params.get("l1_ratio", 0.5),
                max_iter=elasticnet_params.get("max_iter", 10000),
            ),
            True,
        ),
        "Random Forest": (
            RandomForestRegressor(
                n_estimators=rf_params.get("n_estimators", 200),
                max_depth=rf_params.get("max_depth", 15),
                random_state=rf_params.get("random_state", 42),
                n_jobs=-1,
            ),
            False,
        ),
        "Gradient Boosting": (
            GradientBoostingRegressor(
                n_estimators=gb_params.get("n_estimators", 200),
                max_depth=gb_params.get("max_depth", 5),
                learning_rate=gb_params.get("learning_rate", 0.1),
                random_state=gb_params.get("random_state", 42),
            ),
            False,
        ),
        "XGBoost": (
            XGBRegressor(
                n_estimators=xgb_params.get("n_estimators", 200),
                max_depth=xgb_params.get("max_depth", 5),
                learning_rate=xgb_params.get("learning_rate", 0.1),
                random_state=xgb_params.get("random_state", 42),
                n_jobs=-1,
                verbosity=0,
            ),
            False,
        ),
    }

    if _HAS_LIGHTGBM:
        models["LightGBM"] = (
            LGBMRegressor(
                n_estimators=lgbm_params.get("n_estimators", 300),
                max_depth=lgbm_params.get("max_depth", 7),
                learning_rate=lgbm_params.get("learning_rate", 0.05),
                subsample=lgbm_params.get("subsample", 0.8),
                colsample_bytree=lgbm_params.get("colsample_bytree", 0.8),
                reg_alpha=lgbm_params.get("reg_alpha", 0.1),
                reg_lambda=lgbm_params.get("reg_lambda", 1.0),
                random_state=lgbm_params.get("random_state", 42),
                n_jobs=-1,
                verbosity=-1,
            ),
            False,
        )

    if _HAS_CATBOOST:
        models["CatBoost"] = (
            CatBoostRegressor(
                iterations=catboost_params.get("iterations", 300),
                depth=catboost_params.get("depth", 6),
                learning_rate=catboost_params.get("learning_rate", 0.05),
                l2_leaf_reg=catboost_params.get("l2_leaf_reg", 3.0),
                subsample=catboost_params.get("subsample", 0.8),
                random_seed=catboost_params.get("random_seed", 42),
                verbose=0,
            ),
            False,
        )

    return models
