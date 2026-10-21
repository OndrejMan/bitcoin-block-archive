"""Write a synthetic Dumplings Scanner/ folder of mainnet-like size.

Only the files Dumplings reads when it resumes are filled. Line counts follow
the published coinjoin counts (Gavenda's thesis, PETS'26); inputs and outputs
per transaction are rough averages, so the result is an order-of-magnitude
stand-in for a real Scanner/, not a copy of one.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

INPUT = "{txid}-{index}-{value}+0014{script}+TxWitnessV0Keyhash"
OUTPUT = "{value}+0014{script}+TxWitnessV0Keyhash"

# file name: (transactions, inputs per transaction, outputs per transaction)
PROFILES = {
    "Wasabi2CoinJoins.txt": (78_000, 150, 200),
    "WasabiCoinJoins.txt": (35_000, 100, 150),
    "SamouraiCoinJoins.txt": (541_000, 5, 5),
    "SamouraiTx0s.txt": (200_000, 2, 10),
    "OtherCoinJoins.txt": (300_000, 10, 10),
}
EMPTY = [
    "Wasabi2PostMixTxs.txt",
    "WasabiPostMixTxs.txt",
    "SamouraiPostMixTxs.txt",
    "OtherCoinJoinPostMixTxs.txt",
]


def line(inputs: int, outputs: int) -> str:
    txid = os.urandom(32).hex()
    block = "00000000000000000002" + os.urandom(22).hex()
    script = os.urandom(20).hex()
    ins = "}{".join(
        INPUT.format(
            txid=os.urandom(32).hex() if i == 0 else txid,
            index=i,
            value=1_000_000 + i,
            script=script,
        )
        for i in range(inputs)
    )
    outs = "}{".join(
        OUTPUT.format(value=500_000 + i, script=script) for i in range(outputs)
    )
    return f"{txid}:::{block}:::7:::1700000000:::{ins}:::{outs}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="directory that becomes Scanner/")
    parser.add_argument(
        "--scale", type=float, default=1.0, help="multiply every line count"
    )
    parser.add_argument(
        "--height", type=int, default=0, help="LastProcessedBlockHeight to write"
    )
    args = parser.parse_args()

    args.target.mkdir(parents=True, exist_ok=True)
    for name, (count, inputs, outputs) in PROFILES.items():
        with open(args.target / name, "w", encoding="ascii") as handle:
            for _ in range(int(count * args.scale)):
                handle.write(line(inputs, outputs))
    for name in EMPTY:
        (args.target / name).touch()
    (args.target / "LastProcessedBlockHeight.txt").write_text(
        str(args.height), encoding="ascii"
    )
    # Dumplings resumes from Checkpoint.txt: height, then each file and length.
    # It fails without it; files it does not list are left untouched.
    sizes = "".join(
        f"{name} {(args.target / name).stat().st_size}\n"
        for name in [*PROFILES, *EMPTY]
    )
    (args.target / "Checkpoint.txt").write_text(
        f"{args.height}\n{sizes}", encoding="ascii"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
