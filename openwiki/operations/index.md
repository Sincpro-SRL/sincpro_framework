# Files

- [Build, test and release](build-and-release.md) - The maintainer's operational surface of sincpro-framework: the Poetry package and its optional extras, the Makefile targets that gate formatting, typing and coverage, and the CI and release workflows that publish to Gemfury and PyPI.
- [Maintaining and extending: compatibility surface, invariants and open caveats](maintaining-and-extending.md) - The change-safety page a maintainer reads before touching sincpro-framework internals: the exported names and hand-written .pyi stubs that form the compatibility surface downstream SDKs import, the layered invariants each anchored to the code that establishes them, the caveats the source itself flags as unverified, and where a new capability belongs.
