"""A known-good script following the prompt contract (also a debugging reference)."""
import json
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

WORK = os.environ.get("GLASSBOX_WORK", "/work")
df = pd.read_csv(os.path.join(WORK, "data.csv"))
target = df.columns[-1]
X, y = df.drop(columns=[target]), df[target]
num = X.select_dtypes(include=np.number).columns.tolist()
cat = [c for c in X.columns if c not in num]
pre = ColumnTransformer([
    ("num", Pipeline([("i", SimpleImputer(strategy="median")), ("s", StandardScaler())]), num),
    ("cat", Pipeline([("i", SimpleImputer(strategy="most_frequent")),
                      ("o", OneHotEncoder(handle_unknown="ignore"))]), cat),
])
pipe = Pipeline([("pre", pre), ("m", RandomForestClassifier(n_estimators=200, random_state=0))])
Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, random_state=0, stratify=y)
pipe.fit(Xtr, ytr)
base = DummyClassifier(strategy="most_frequent").fit(Xtr, ytr)
pi = permutation_importance(pipe, Xte, yte, n_repeats=10, random_state=0)
order = np.argsort(pi.importances_mean)[::-1][:10]
imps = [{"feature": X.columns[i], "mean": round(float(pi.importances_mean[i]), 4),
         "std": round(float(pi.importances_std[i]), 4)} for i in order]
os.makedirs("/tmp/out" if not os.environ.get("GLASSBOX_OUT") else os.environ["GLASSBOX_OUT"], exist_ok=True)
plt.figure(figsize=(6, 3.5))
plt.barh([d["feature"] for d in imps][::-1], [d["mean"] for d in imps][::-1])
plt.tight_layout()
plt.savefig(os.path.join(os.environ.get("GLASSBOX_OUT", "/tmp/out"), "importance.png"), dpi=110)
print(json.dumps({"task": "classification", "target": target, "n_rows": len(df), "n_features": X.shape[1],
                  "metric_name": "accuracy", "model_score": round(float(pipe.score(Xte, yte)), 4),
                  "baseline_score": round(float(base.score(Xte, yte)), 4), "importances": imps,
                  "caveats": ["synthetic sample data"]}))
