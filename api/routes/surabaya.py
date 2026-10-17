from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/surabaya", tags=["Surabaya live"])


@router.get("/snapshot")
def snapshot(request: Request):
    service = getattr(request.app.state, "surabaya", None)
    if service is None:
        raise HTTPException(503, "Layanan Surabaya belum tersedia.")
    return service.get_snapshot()
