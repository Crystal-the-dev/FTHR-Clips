"""Stable user-facing error codes shared by the app and its documentation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppFailureCode:
    """One documented failure title and the reason it can be emitted."""

    code: str
    title: str
    category: str
    explanation: str


APP_FAILURE_CODES: tuple[AppFailureCode, ...] = (
    AppFailureCode(
        'Error 001', 'CAPTURE FAILED', 'Capture',
        'Capture health reported a backend failure or the capture startup '
        'sequence failed. The detail text contains the reported reason.'),
    AppFailureCode(
        'Error 002', 'CAPTURE UNAVAILABLE', 'Capture',
        'A save was requested while the capture engine was not connected, so '
        'there was no active replay buffer to address.'),
    AppFailureCode(
        'Error 003', 'ENGINE NOT FOUND', 'Capture',
        'The FTHRcapture executable could not be found at the expected install '
        'or development path.'),
    AppFailureCode(
        'Error 004', 'ENGINE NOT RESPONDING', 'Capture',
        'The capture process was launched but did not complete its IPC startup '
        'handshake.'),
    AppFailureCode(
        'Error 005', 'ENGINE COULD NOT START', 'Capture',
        'A capture startup attempt failed before a usable engine connection was '
        'established.'),
    AppFailureCode(
        'Error 006', 'ENGINE STOPPED', 'Capture',
        'The capture process exited unexpectedly. The process exit code is '
        'included in the message detail.'),
    AppFailureCode(
        'Error 007', 'MANUAL RECORDING FAILED', 'Recording',
        'A manual recording could not be completed or finalized. Recoverable '
        'video fragments may be mentioned in the detail.'),
    AppFailureCode(
        'Error 008', 'RECORDING COULD NOT START', 'Recording',
        'The manual recording request could not be handed to the capture '
        'engine.'),
    AppFailureCode(
        'Error 009', 'RECORDING COULD NOT STOP', 'Recording',
        'The capture engine was unavailable when a manual recording stop was '
        'requested.'),
    AppFailureCode(
        'Error 010', 'RECORDING FOLDER UNAVAILABLE', 'Recording',
        'The configured manual-recording folder could not be created or '
        'accessed.'),
    AppFailureCode(
        'Error 011', 'RECORDING PATH TOO LONG', 'Recording',
        'The manual-recording output path exceeds the native engine path '
        'limit.'),
    AppFailureCode(
        'Error 012', 'NOT ENOUGH DISK SPACE', 'Recording',
        'Manual recording was blocked by the preflight free-space threshold.'),
    AppFailureCode(
        'Error 013', 'CLIP NOT SAVED', 'Clip saving',
        'Capture health rejected the save because fresh replay data was not '
        'currently usable. The detail says whether capture is starting, '
        'recovering, or stale.'),
    AppFailureCode(
        'Error 014', 'CLIP SAVE FAILED', 'Clip saving',
        'The local clip folder, save command submission, or save setup failed '
        'before a completed clip was produced.'),
    AppFailureCode(
        'Error 015', 'CLIP WAS NOT SAVED', 'Clip saving',
        'The capture engine reported a terminal save failure, an incomplete '
        'path, or a missing/empty output file.'),
    AppFailureCode(
        'Error 016', 'CLIP PATH TOO LONG', 'Clip saving',
        'The requested clip path exceeds the Windows shared-memory path field '
        'limit.'),
    AppFailureCode(
        'Error 017', 'CLIP FINALIZATION FAILED', 'Clip saving',
        'The base clip could not be made available as a completed final file '
        'after the save operation.'),
    AppFailureCode(
        'Error 018', 'EXPORT FAILED', 'Export and sharing',
        'An edited clip export failed validation, encoding, folder creation, '
        'or file publication.'),
    AppFailureCode(
        'Error 019', 'SHARE FAILED', 'Export and sharing',
        'The share workflow could not create or publish its exported clip.'),
    AppFailureCode(
        'Error 020', 'UPLOAD FAILED', 'Upload',
        'The upload provider rejected or could not complete the upload. The '
        'provider or retry detail is passed through when available.'),
    AppFailureCode(
        'Error 021', 'UPLOAD NOT CONFIGURED', 'Upload',
        'An upload was requested without an installed and enabled Upload '
        'Extension.'),
    AppFailureCode(
        'Error 022', 'UPLOAD CONNECTION FAILED', 'Upload',
        'The upload settings Test Connection request failed. The provider '
        'response is used as the detail.'),
    AppFailureCode(
        'Error 023', 'COMPRESSION FAILED', 'Upload',
        'The optional compressed upload copy could not be created, verified, '
        'or brought under the provider size limit.'),
)

APP_FAILURE_CODE_BY_TITLE = {
    item.title: item for item in APP_FAILURE_CODES
}


def format_error_title(title: str) -> str:
    """Return the bottom-bar title with its stable code when documented."""

    normalized = str(title or '').strip().upper()
    definition = APP_FAILURE_CODE_BY_TITLE.get(normalized)
    if definition is None:
        return normalized
    return f'[{definition.code}] {definition.title}'
