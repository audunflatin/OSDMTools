"""Tilstand i minnet (per bruker / per jobb). Krever at appen kjører som én instans."""
import threading
import time
import uuid

from fastapi import HTTPException, Request


TEN_STORE: dict[str, dict] = {}        # user_email → {"table": list[(fra_km, til_km, pris)], "created_at": float}
GENERATION_PROGRESS: dict[str, dict] = {}  # user_email → {"status": str, "percent": int, "created_at": float}
OSDM_STORE: dict[str, dict] = {}       # user_email → {"filename": str, "content": str}
INPUT_OSDM_STORE: dict[str, dict] = {} # user_email → {"filename": str, "bytes": bytes, "created_at": float}
FIX_OSDM_STORE: dict[str, dict] = {}  # user_email → {"filename": str, "content": bytes}
EDIT_STORE: dict[str, dict] = {}       # user_email → osdm_editor store entry + {"filename": str, "created_at": float}
XLSX_JOBS: dict = {}        # job_id → {status, result, error, filename, percent, rows, owner, created_at}
VALIDATION_JOBS: dict = {}  # job_id → {status, percent, phase, start_time, file_size, result, error, owner}
PARSE_JOBS: dict = {}       # job_id → {status, percent, phase, start_time, file_size, result, error, owner}

_JOB_TTL = 2 * 3600  # 2 timer

def _cleanup_jobs():
    while True:
        time.sleep(600)
        cutoff = time.time() - _JOB_TTL
        for jobs in (XLSX_JOBS, VALIDATION_JOBS, PARSE_JOBS, OSDM_STORE, INPUT_OSDM_STORE, FIX_OSDM_STORE, EDIT_STORE,
                     TEN_STORE, GENERATION_PROGRESS):
            stale = [k for k, v in list(jobs.items()) if v.get("created_at", 0) < cutoff]
            for k in stale:
                jobs.pop(k, None)

threading.Thread(target=_cleanup_jobs, daemon=True).start()


# ---------------------------------------------------------------------------
# Bakgrunnsjobber: felles opprettelse, eierskap og fremdrift
# ---------------------------------------------------------------------------

def create_job(jobs: dict, owner: str, **fields) -> str:
    """Opprett en jobb (status «running») eid av `owner`. Returnerer job_id."""
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "running", "percent": 0, "result": None, "error": None,
        "owner": owner, "created_at": time.time(), **fields,
    }
    return job_id


def owned_job(jobs: dict, job_id: str, request: Request) -> dict | None:
    """Hent jobb; None hvis den ikke finnes, HTTP 403 hvis en annen bruker eier den."""
    job = jobs.get(job_id)
    if job is not None and job.get("owner") != request.session.get("user_email"):
        raise HTTPException(status_code=403, detail="Ikke tilgang")
    return job


def start_file_job(jobs: dict, owner: str, target, file_bytes: bytes) -> str:
    """Start en filjobb (validering/parsing) i bakgrunnen: target(job_id, file_bytes)."""
    job_id = create_job(jobs, owner, phase="parsing", start_time=time.time(), file_size=len(file_bytes))
    threading.Thread(target=target, args=(job_id, file_bytes), daemon=True).start()
    return job_id


def file_job_progress(jobs: dict, job_id: str, request: Request) -> dict:
    """Fremdriftssvar for filjobber. Lesefasen estimeres ut fra filstørrelse (~20 MB/s).
    Ferdige jobber returneres én gang og fjernes."""
    job = owned_job(jobs, job_id, request)
    if not job:
        return {"status": "error", "error": "Jobb ikke funnet"}
    # Sjekk ferdig/feil først: en jobb som feiler i lesefasen (ugyldig JSON) står
    # fortsatt i phase «parsing», og ville ellers vist «reading» for alltid.
    if job["status"] in ("done", "error"):
        jobs.pop(job_id, None)
        return {"status": job["status"], "percent": job.get("percent", 100),
                "result": job.get("result"), "error": job.get("error")}
    if job.get("phase") == "parsing":
        elapsed = time.time() - job.get("start_time", time.time())
        estimated_s = max(2.0, job.get("file_size", 0) / (1024 * 1024) / 20)
        return {"status": "running", "percent": min(26, int(elapsed / estimated_s * 26)), "stage": "reading"}
    return {"status": "running", "percent": job.get("percent", 0), "stage": "validating"}
