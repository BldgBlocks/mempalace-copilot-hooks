---
name: mpingest
description: Ingest exported chat transcripts one at a time through the deployed MemPalace hook path.
argument-hint: file-or-folder paths containing exported chat transcript files
agent: agent
---

# MemPalace Prompt: Imported Chat Ingest

Use this prompt when the user provides exported chat transcript files or folders from another platform and wants them ingested through the same hook-driven MemPalace path used for normal Copilot chat capture.

## Goal

Process the supplied transcript files one at a time through the deployed hook script so imported chats receive the same handling as live Copilot chats:

- normalized `transcript.txt` export path
- explicit titled `chat_transcript_full` fallback drawer on `transcript.full.raw`
- deterministic closets for the explicit long-form record
- normal upstream `mempalace mine --mode convos --extract exchange` pass on `transcript.txt`

## Required Behavior

1. Treat the user-provided input as one or more file paths or folder paths.
2. If a folder is provided, enumerate candidate transcript files recursively and ingest them one at a time.
3. Do not batch-import by calling `mempalace mine` directly on the external folder alone. The point is to run the deployed hook path per file.
4. Prefer the deployed desktop hook script at `/home/admin/.config/Code/User/copilot-hooks/export_chat_hook.py` so behavior matches the live environment.
5. Preserve a stable `cwd` when constructing the hook payload. If the user does not specify a target workspace, use the current workspace root.
6. For each file, send a JSON payload to the hook script containing at minimum:
   - `hook_event_name`: `Stop`
   - `transcript_path`: absolute path to the file being ingested
   - `cwd`: workspace root or chosen target root
   - `timestamp`: current ISO timestamp
   - `sessionId`: a stable identifier derived from the file path
7. Ingest files sequentially, not concurrently.
8. After processing, summarize:
   - how many files were attempted
   - how many succeeded or failed
   - any hook warnings or mine failures

## File Selection Guidance

- Accept JSONL transcript files if they follow the hook-readable event format.
- Accept already-normalized `.txt` transcripts if the user wants them re-filed through the same fallback-plus-mine path.
- Skip obvious non-transcript files and say what was skipped.

## Safety and Consistency Rules

- Do not manually file ad hoc drawers instead of running the hook.
- Do not change the palace path unless the user explicitly asks.
- Do not delete unrelated drawers.
- If re-ingesting files that were already processed, prefer deterministic replacement through the hook rather than creating alternate manual records.

## Suggested Execution Shape

If the user gives a folder, first discover candidate files, then iterate one file at a time by piping a JSON payload into the deployed hook script.

## Output Style

Keep the response concise.
Report the processed files and any failures clearly.
