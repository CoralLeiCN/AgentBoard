"""Serve the validation-selected ModernBERT checkpoint directly on Spark."""

import argparse
from pathlib import Path

from classifier.artifacts import manifest_hash, read_json


def serve(selection: Path, host: str, port: int):
    import uvicorn

    from classifier.encoder_runtime import Encoder
    from classifier.encoder_service import create_app

    chosen = read_json(selection)
    checkpoint = Path(chosen["selected_path"])
    if manifest_hash(checkpoint) != chosen["checkpoint_sha256"]:
        raise ValueError("Selection checkpoint hash differs")
    app = create_app(lambda: Encoder(checkpoint))
    uvicorn.run(app, host=host, port=port, workers=1, access_log=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    serve(args.selection, args.host, args.port)
