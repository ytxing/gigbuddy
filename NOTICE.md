# GigBuddy notices

GigBuddy v0.1.0-alpha.8 is source-only. The repository does not include model
files, dry-input audio, the local SQLite cache, compiled binaries, or the C++
dependency checkout. `scripts/bootstrap_third_party.sh` fetches the pinned
NeuralAudio source and its submodules at build time; their license files remain
in the downloaded dependency tree.

The current build path uses these external components:

- NeuralAudio, MIT, upstream commit `49100f90603afc83d810a960faf30e8326edc4bc`.
- NeuralAmpModelerCore, MIT, pinned by the NeuralAudio submodule.
- RTNeural, BSD 3-Clause, pinned by the NeuralAudio submodule.
- math_approx, BSD 3-Clause, pinned by the NeuralAudio submodule.
- Eigen, MPL-2.0 and component-specific permissive notices, consumed by RTNeural.
- PortAudio, its own upstream license, linked by the macOS realtime binary.
- Textual, Rich, NumPy, and their respective Python package licenses.

TONE3000 metadata and downloaded `.nam`/`.wav` files are user-fetched runtime
data. They are not bundled or relicensed by GigBuddy. Users must follow the
source service's terms and each model/audio creator's redistribution terms.
