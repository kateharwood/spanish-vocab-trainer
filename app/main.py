import logging
import os
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.store import ARCHIVED_GROUPS, GROUP_LABELS, GROUPS, get_store
from app.definitions import DefinitionHelper, anthropic_suggester
from app.study import StudyService, build_entries, iso, utcnow

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = FastAPI(title="Spanish vocab sorter")
store = get_store()


@app.middleware("http")
async def revalidate_pages(request: Request, call_next):
    # Without this, browsers keep running a stale app.js after the code changes.
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response
study = StudyService(store)
definitions = DefinitionHelper(store, anthropic_suggester())
log = logging.getLogger(__name__)


class AssignRequest(BaseModel):
    ranks: List[int]
    group_code: Optional[str] = None
    # Giving a word a level counts as choosing to keep studying it.
    keep: bool = False


class RestoreRequest(BaseModel):
    updates: Dict[int, Optional[str]]
    families: Optional[Dict[int, Optional[int]]] = None
    # Words whose keep-or-archive choice the undone change made.
    undecide: Optional[List[int]] = None


class MergeRequest(BaseModel):
    ranks: List[int]
    group_code: Optional[str] = None


class UnmergeRequest(BaseModel):
    ranks: List[int]


class StudyReviewRequest(BaseModel):
    queue_id: str
    rating: str


class StudyGroupRequest(BaseModel):
    ranks: List[int]
    group_code: str


class StudyCloseRequest(BaseModel):
    status: str


class DefinitionDecisionRequest(BaseModel):
    ranks: List[int]


class DefinitionRequest(BaseModel):
    families: List[List[int]]


class ArchiveRequest(BaseModel):
    ranks: List[int]
    archived: bool = True
    # Undo: bring back the keep-or-archive prompt for words never decided on.
    reset_decision: bool = False


class KeepRequest(BaseModel):
    ranks: List[int]


class WordEditRequest(BaseModel):
    spanish: str
    english: str
    alt_english: Optional[str] = None


def ranks_in_families(ranks: List[int], words: List[Dict]) -> List[int]:
    """Widen a set of ranks to every variant of the families they belong to."""
    by_rank = {w["rank"]: w for w in words}
    wanted = set()
    for rank in ranks:
        word = by_rank.get(rank)
        if word is None:
            raise HTTPException(status_code=404, detail="Unknown rank %s" % rank)
        wanted.add(word.get("family_id") or rank)
    return [w["rank"] for w in words if (w.get("family_id") or w["rank"]) in wanted]


@app.get("/api/words")
def list_words():
    entries = build_entries(store.list_words())
    counts = {group: 0 for group in GROUPS}
    unsorted_count = 0
    for entry in entries:
        group = entry["group_code"]
        if group in counts:
            counts[group] += 1
        else:
            unsorted_count += 1
    return {
        "words": entries,
        "counts": counts,
        "unsorted": unsorted_count,
        "groups": [{"code": code, "label": GROUP_LABELS[code]} for code in GROUPS],
        "backend": store.backend,
    }


@app.post("/api/assign")
def assign(request: AssignRequest):
    if request.group_code is not None and request.group_code not in GROUPS:
        raise HTTPException(status_code=400, detail="Unknown group %s" % request.group_code)
    ranks = ranks_in_families(request.ranks, store.list_words())
    updated = store.assign(ranks, request.group_code)
    if request.keep and request.group_code is not None:
        store.mark_decided(ranks, iso(utcnow()))
    # Archived words should stop showing up in a study session already under way.
    if request.group_code is None or request.group_code in ARCHIVED_GROUPS:
        active = store.get_active_session()
        if active:
            store.complete_pending_for_ranks(active["id"], ranks, iso(utcnow()))
    return {"updated": updated, "ranks": ranks}


@app.post("/api/archive")
def archive(request: ArchiveRequest):
    try:
        ranks = study.set_archived(
            request.ranks, request.archived, reset_decision=request.reset_decision
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ranks": ranks, "archived": request.archived}


@app.post("/api/keep")
def keep(request: KeepRequest):
    """Keep studying these words: their halo and archive prompt go away."""
    try:
        return {"ranks": study.keep(request.ranks)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/restore")
def restore(request: RestoreRequest):
    for group in request.updates.values():
        if group is not None and group not in GROUPS:
            raise HTTPException(status_code=400, detail="Unknown group %s" % group)
    if request.families is not None:
        store.set_families(request.families)
    if request.undecide:
        store.clear_decided(request.undecide)
    return {"updated": store.assign_many(request.updates)}


@app.post("/api/merge")
def merge(request: MergeRequest):
    if request.group_code is not None and request.group_code not in GROUPS:
        raise HTTPException(status_code=400, detail="Unknown group %s" % request.group_code)

    words = store.list_words()
    ranks = ranks_in_families(request.ranks, words)
    if len(ranks) < 2:
        raise HTTPException(status_code=400, detail="Merging needs at least two words")

    # The family is named after its most frequent member, so an entry keeps its
    # place in the list after merging.
    family_id = min(ranks)
    store.set_family(ranks, family_id)

    by_rank = {w["rank"]: w for w in words}
    previous = {rank: by_rank[rank].get("group_code") for rank in ranks}
    if request.group_code is not None or len(set(previous.values())) > 1:
        store.assign(ranks, request.group_code)

    return {"family_id": family_id, "ranks": ranks, "previous_groups": previous}


@app.post("/api/unmerge")
def unmerge(request: UnmergeRequest):
    ranks = ranks_in_families(request.ranks, store.list_words())
    # Each word becomes a family of one rather than having no family, which
    # tells scripts/build_families.py not to merge it back together.
    store.set_families({rank: rank for rank in ranks})
    return {"ranks": ranks}


@app.post("/api/study/session")
def start_or_resume_study():
    state = study.start_or_resume()
    try:
        definitions.queue_for_session(state["session_id"])
    except Exception:
        # Most likely the suggestions table has not been created in Supabase
        # yet. Studying should never fail because of it.
        log.exception("Could not queue definition suggestions")
    return state


@app.get("/api/definitions")
def get_definitions(ranks: List[int] = Query(...)):
    if not definitions.enabled:
        return {"enabled": False, "working": False, "suggestions": []}
    try:
        return definitions.for_ranks(ranks)
    except Exception:
        log.exception("Could not read definition suggestions")
        return {"enabled": False, "working": False, "suggestions": []}


@app.post("/api/definitions/request")
def request_definitions(request: DefinitionRequest):
    """Ask for suggestions ahead of time, e.g. for the next few cards in Sort."""
    if not definitions.enabled:
        return {"queued": 0}
    try:
        # A handful at a time is all the Sort screen needs.
        return {"queued": definitions.queue_families(request.families[:10])}
    except Exception:
        log.exception("Could not queue definition suggestions")
        return {"queued": 0}


@app.post("/api/definitions/accept")
def accept_definitions(request: DefinitionDecisionRequest):
    return {"accepted": definitions.resolve(request.ranks, accept=True)}


@app.post("/api/definitions/dismiss")
def dismiss_definitions(request: DefinitionDecisionRequest):
    definitions.resolve(request.ranks, accept=False)
    return {"dismissed": True}


@app.get("/api/study/progress")
def get_study_progress():
    return study.overall_progress()


@app.post("/api/study/session/{session_id}/review")
def review_study_card(session_id: str, request: StudyReviewRequest):
    try:
        return study.review(session_id, request.queue_id, request.rating)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/study/session/{session_id}/group")
def change_study_group(session_id: str, request: StudyGroupRequest):
    try:
        return study.change_group(session_id, request.ranks, request.group_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/study/session/{session_id}/close")
def close_study_session(session_id: str, request: StudyCloseRequest):
    try:
        study.close_session(session_id, request.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": request.status}


@app.patch("/api/words/{rank}")
def edit_word(rank: int, request: WordEditRequest):
    try:
        study.update_variant(rank, request.spanish, request.english, request.alt_english)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"updated": 1}


@app.post("/api/words/{rank}/main")
def set_main_word(rank: int):
    try:
        return {"ranks": study.set_main_word(rank)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
