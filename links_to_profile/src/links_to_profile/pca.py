"""Phase 5: fit PCA on the embedding corpus subset.

Reads:
    embeddings/{page_id}.npy           — float32 (4096,) per page
    data/corpus_pages.jsonl            — for filtering by n_bookmarks

Writes:
    axes/pca_model.npz                 — mean_, components_, explained_variance_,
                                          explained_variance_ratio_, singular_values_
    axes/pca_meta.json                 — n_components, n_features, n_samples, subset filter
    axes/page_coords.parquet           — page_id + pc1..pcK for all fit pages
    axes/scree.png                     — cumulative explained variance plot

NOTE on serialization: the plan called for axes/pca_model.pkl via joblib, but
joblib pickles under the hood and our environment flags that as unsafe. PCA is
just a linear transform — mean + components + a handful of scalars — and
round-trips losslessly through npz with no executable payload. Use load_pca()
below to reconstruct an equivalent sklearn PCA.

Default filter: pages with n_bookmarks >= config.corpus.min_bookmark_count (3).
Auto-switches to IncrementalPCA above 50k pages so the full 181k corpus fits.

Run:
    uv run python -m links_to_profile.pca
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
import yaml

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from sklearn.decomposition import PCA, IncrementalPCA  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
CORPUS_PATH = PROJECT_ROOT / "data" / "corpus_pages.jsonl"
EMB_DIR = PROJECT_ROOT / "embeddings"
AXES_DIR = PROJECT_ROOT / "axes"

INCREMENTAL_THRESHOLD = 50_000


def iter_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            yield json.loads(line)


def collect_subset(min_bookmarks: int) -> tuple[list[int], int]:
    """(page_ids_with_embeddings, total_eligible). Only pages whose .npy exists."""
    eligible = [int(r["page_id"]) for r in iter_jsonl(CORPUS_PATH)
                if int(r.get("n_bookmarks", 0)) >= min_bookmarks]
    have = [pid for pid in eligible if (EMB_DIR / f"{pid}.npy").exists()]
    return have, len(eligible)


def fit_dense(ids: list[int], n_components: int) -> tuple[PCA, np.ndarray]:
    X = np.vstack([np.load(EMB_DIR / f"{pid}.npy") for pid in ids])
    pca = PCA(n_components=n_components, random_state=0).fit(X)
    return pca, pca.transform(X)


def fit_incremental(ids: list[int], n_components: int, batch_size: int = 4096
                    ) -> tuple[IncrementalPCA, np.ndarray]:
    pca = IncrementalPCA(n_components=n_components, batch_size=batch_size)
    for i in range(0, len(ids), batch_size):
        batch = np.vstack([np.load(EMB_DIR / f"{pid}.npy") for pid in ids[i:i + batch_size]])
        pca.partial_fit(batch)
    coords = np.empty((len(ids), n_components), dtype=np.float32)
    for i in range(0, len(ids), batch_size):
        batch = np.vstack([np.load(EMB_DIR / f"{pid}.npy") for pid in ids[i:i + batch_size]])
        coords[i:i + batch.shape[0]] = pca.transform(batch).astype(np.float32)
    return pca, coords


def save_pca_npz(pca, path: Path) -> None:
    """Serialize the linear-algebra contents of a fitted PCA without pickling."""
    np.savez_compressed(
        path,
        mean_=pca.mean_.astype(np.float32),
        components_=pca.components_.astype(np.float32),
        explained_variance_=pca.explained_variance_.astype(np.float32),
        explained_variance_ratio_=pca.explained_variance_ratio_.astype(np.float32),
        singular_values_=getattr(pca, "singular_values_",
                                 np.zeros_like(pca.explained_variance_)).astype(np.float32),
    )


def load_pca(npz_path: Path) -> PCA:
    """Reconstruct a usable sklearn PCA from save_pca_npz output. Sufficient for
    .transform() — the only thing downstream phases need."""
    d = np.load(npz_path)
    pca = PCA(n_components=d["components_"].shape[0])
    pca.mean_ = d["mean_"]
    pca.components_ = d["components_"]
    pca.explained_variance_ = d["explained_variance_"]
    pca.explained_variance_ratio_ = d["explained_variance_ratio_"]
    pca.singular_values_ = d["singular_values_"]
    pca.n_features_in_ = d["components_"].shape[1]
    pca.n_components_ = d["components_"].shape[0]
    pca.whiten = False
    return pca


def main() -> None:
    config = yaml.safe_load(CONFIG_PATH.read_text())
    min_bookmarks = int(config["corpus"]["min_bookmark_count"])
    n_components = int(config["pca"]["n_components"])

    AXES_DIR.mkdir(parents=True, exist_ok=True)

    ids, total_eligible = collect_subset(min_bookmarks)
    print(f"corpus filter: n_bookmarks >= {min_bookmarks}  "
          f"eligible_pages={total_eligible}  with_embeddings={len(ids)}")
    if len(ids) < n_components * 2:
        raise RuntimeError(
            f"too few embedded pages ({len(ids)}) for {n_components}-component PCA"
        )

    if len(ids) > INCREMENTAL_THRESHOLD:
        print(f"using IncrementalPCA (subset > {INCREMENTAL_THRESHOLD})")
        pca, coords = fit_incremental(ids, n_components)
    else:
        print("using dense PCA")
        pca, coords = fit_dense(ids, n_components)

    save_pca_npz(pca, AXES_DIR / "pca_model.npz")
    meta = {
        "n_components": int(n_components),
        "n_features": int(pca.n_features_in_),
        "n_samples": int(len(ids)),
        "min_bookmark_count": int(min_bookmarks),
        "fitter": "IncrementalPCA" if len(ids) > INCREMENTAL_THRESHOLD else "PCA",
    }
    (AXES_DIR / "pca_meta.json").write_text(json.dumps(meta, indent=2))

    coord_df = pd.DataFrame(
        {"page_id": ids, **{f"pc{i+1}": coords[:, i] for i in range(n_components)}}
    )
    coord_df.to_parquet(AXES_DIR / "page_coords.parquet")

    evr = pca.explained_variance_ratio_
    cumvar = np.cumsum(evr)
    fig, ax = plt.subplots(figsize=(6, 4))
    xs = np.arange(1, len(evr) + 1)
    ax.bar(xs, evr, alpha=0.4, label="per-PC")
    ax.plot(xs, cumvar, "-o", color="crimson", label="cumulative")
    ax.set_xlabel("PC index")
    ax.set_ylabel("explained variance ratio")
    ax.set_title(f"PCA scree (subset n={len(ids)}, dim={pca.n_features_in_})")
    ax.legend()
    fig.tight_layout()
    fig.savefig(AXES_DIR / "scree.png", dpi=120)

    print("  pca_model.npz, pca_meta.json, page_coords.parquet, scree.png written")
    print(f"  explained variance ratios: {[round(float(v), 4) for v in evr]}")
    print(f"  cumulative @ top {n_components}: {float(cumvar[-1]):.4f}")


if __name__ == "__main__":
    main()
