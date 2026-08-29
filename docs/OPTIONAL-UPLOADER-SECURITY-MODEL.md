# Optional uploader security model

Release model date: 2026-08-24

## Package boundary

FTHR Clips Core contains consent, verified installation, queueing, and local
subprocess coordination. It contains no Catbox or Lustful HTTP client.

Two dormant packages ship with the Windows bundle:

1. `FTHR-Uploader.fthrplugin` contains the one-shot network-enabled uploader.
2. `FTHR-Hardware-Identity.fthrplugin` contains the one-shot local machine-ID
   reader required only by Lustful.

Neither archive is executable or extracted by the installer.

## Uploader activation

1. The user enables the optional uploader in Settings.
2. Core verifies the uploader archive against the SHA-256 bound into that Core
   release.
3. Core displays the Terms of Service and Privacy Policy stored in the verified
   archive.
4. Both acknowledgements must be checked before installation is available.
5. Core verifies the declared archive contents and every payload hash, extracts
   the uploader into the current user's local application-data directory, and
   writes an activation receipt.
6. Disabling uploads keeps the package installed but prevents its process from
   accepting network actions.

## Provider consent

Catbox and Lustful each have a versioned, provider-specific consent gate. The
uploader process checks that the current provider version was accepted before
it performs a connection test, account action, or upload.

- Catbox legal page: <https://catbox.moe/legal.php>
- Lustful terms: <https://fthr.lustful.wtf/tos>
- Lustful privacy: <https://fthr.lustful.wtf/privacy>

## Lustful Hardware Identity activation

Selecting Lustful does not give the general uploader access to machine identity.
After Lustful consent, Core presents the separately bundled Hardware Identity
terms and privacy notice. Only explicit acceptance installs that second package.

Hardware Identity reads Windows MachineGuid (or the Linux OS machine-id),
derives a namespaced UUID locally, returns only that UUID, and exits. Raw OS
identifiers are not written to disk or returned to Core. The capability has no
networking code. Catbox never installs or invokes it.

## Runtime enforcement

Every one-shot process validates:

- package ID and version;
- accepted legal-policy versions;
- a random activation ID shared by receipt and request;
- its executable SHA-256;
- for uploader network actions, the current enabled setting and provider consent;
- for file actions, that the file is inside a Core-approved clip root.

Core communicates with both processes using one JSON request on standard input
and one JSON response on standard output. Neither package runs while idle.

## Build and release binding

Run `python tools/build_optional_uploaders.py` before freezing Core. The script:

1. builds both one-shot executables independently;
2. signs them first when a release signing command is supplied;
3. creates exact-content `.fthrplugin` archives;
4. hashes both archives and generates `core/uploader_bundle_manifest.py`;
5. lets `FTHR.spec` embed only the dormant archives in the Core distribution.

The normal Windows release driver performs this step automatically. Inno Setup
copies the dormant archives as part of the application bundle and removes any
separately activated package files during uninstall.
