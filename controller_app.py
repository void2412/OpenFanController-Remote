from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from base_logger import logger, set_logger_level
from fan_control_service import CurveEngine, FanControlConfigStore, RemoteSensorReader
from hardware_interface import FanControllerHardwareInterface


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "web"
ENV_PATH = ROOT / ".env"


@dataclass(frozen=True)
class AppSettings:
    host: str = "127.0.0.1"
    port: int = 8088
    controller_poll_interval: float = 2.0
    curve_poll_interval: float = 2.0
    log_level: str = "info"


class SensorSourceRequest(BaseModel):
    name: str
    url: str


class CurveTargetRequest(BaseModel):
    controller_id: str
    fan: str = "all"


class CurvePointRequest(BaseModel):
    temp: float
    pwm: float = Field(ge=0, le=100)


class CurveRequest(BaseModel):
    id: str | None = None
    name: str
    sensor_ids: List[str]
    targets: List[CurveTargetRequest]
    curve_type: str = "linear"
    points: List[CurvePointRequest]
    hysteresis: float = Field(default=2.0, ge=0)
    response_time: float = Field(default=1.0, ge=0)
    enabled: bool = True


class CurveEnabledRequest(BaseModel):
    enabled: bool


class ControllerNameRequest(BaseModel):
    name: str = ""


class PwmRequest(BaseModel):
    pwm: float = Field(ge=0, le=100)


class AppState:
    def __init__(self, controller_poll_interval: float, curve_poll_interval: float):
        self.hardware = FanControllerHardwareInterface(poll_interval=controller_poll_interval)
        self.store = FanControlConfigStore()
        self.sensors = RemoteSensorReader(self.store)
        self.curves = CurveEngine(self.hardware, self.store, self.sensors, interval=curve_poll_interval)

    def start(self) -> None:
        self.hardware.start()
        self.curves.start()

    def stop(self) -> None:
        self.curves.stop()
        self.hardware.stop()


def load_settings(env_path: Path = ENV_PATH) -> AppSettings:
    load_dotenv(env_path, override=False)

    import os

    fallback_poll_interval = os.getenv("OFC_POLL_INTERVAL", "2.0")

    return AppSettings(
        host=os.getenv("OFC_HOST", "127.0.0.1"),
        port=int(os.getenv("OFC_PORT", "8088")),
        controller_poll_interval=float(os.getenv("OFC_CONTROLLER_POLL_INTERVAL", fallback_poll_interval)),
        curve_poll_interval=float(os.getenv("OFC_CURVE_POLL_INTERVAL", fallback_poll_interval)),
        log_level=os.getenv("OFC_LOG_LEVEL", "info"),
    )


settings = load_settings()
state = AppState(
    controller_poll_interval=settings.controller_poll_interval,
    curve_poll_interval=settings.curve_poll_interval,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    set_logger_level(settings.log_level)
    state.start()
    logger.info("OpenFanController browser app running at http://%s:%s", settings.host, settings.port)
    try:
        yield
    finally:
        logger.info("Shutting down")
        state.stop()


app = FastAPI(
    title="OpenFanController Remote",
    version="0.1.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.get("/")
def index():
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/api/status")
def get_status() -> Dict[str, Any]:
    return {
        "controllers": controller_snapshots(),
        "sources": [source.to_dict() for source in state.store.snapshot_sources()],
        "sensors": state.curves.last_sensors,
        "sensor_errors": state.sensors.last_errors,
        "curves": [curve.to_dict() for curve in state.store.snapshot_curves()],
    }


@app.get("/api/controllers")
def list_controllers() -> List[Dict[str, object]]:
    return controller_snapshots()


@app.post("/api/controllers/{controller_id}/name")
def set_controller_name(controller_id: str, request: ControllerNameRequest) -> Dict[str, bool]:
    state.store.set_controller_name(controller_id, request.name)
    return {"ok": True}


@app.get("/api/controllers/{controller_id}/fans")
def get_fans(controller_id: str) -> Dict[int, int]:
    return _or_http_error(lambda: state.hardware.get_all_fan_rpm(controller_id))


@app.post("/api/controllers/{controller_id}/fans/all/pwm")
def set_all_fans_pwm(controller_id: str, request: PwmRequest) -> Dict[str, object]:
    return {"response": _or_http_error(lambda: state.hardware.set_all_fan_pwm(controller_id, request.pwm))}


@app.post("/api/controllers/{controller_id}/fans/{fan}/pwm")
def set_fan_pwm(controller_id: str, fan: int, request: PwmRequest) -> Dict[str, object]:
    return {"response": _or_http_error(lambda: state.hardware.set_fan_pwm(controller_id, fan, request.pwm))}


@app.get("/api/sources")
def list_sources() -> List[Dict[str, str]]:
    return [source.to_dict() for source in state.store.snapshot_sources()]


@app.post("/api/sources", status_code=201)
def add_source(request: SensorSourceRequest) -> Dict[str, str]:
    source = _or_http_error(lambda: state.store.add_source(request.name, request.url))
    state.curves.last_sensors = state.sensors.read_sensors()
    return source.to_dict()


@app.delete("/api/sources/{source_name}")
def remove_source(source_name: str) -> Dict[str, bool]:
    state.store.remove_source(source_name)
    return {"ok": True}


@app.get("/api/sensors")
def read_sensors() -> Dict[str, object]:
    state.curves.last_sensors = state.sensors.read_sensors()
    return {"sensors": state.curves.last_sensors, "errors": state.sensors.last_errors}


@app.get("/api/curves")
def list_curves() -> List[Dict[str, object]]:
    return [curve.to_dict() for curve in state.store.snapshot_curves()]


@app.post("/api/curves", status_code=201)
def save_curve(request: CurveRequest) -> Dict[str, object]:
    curve = state.store.upsert_curve(request.model_dump())
    return curve.to_dict()


@app.post("/api/curves/{curve_id}/enabled")
def set_curve_enabled(curve_id: str, request: CurveEnabledRequest) -> Dict[str, object]:
    curve = _or_http_error(lambda: state.store.set_curve_enabled(curve_id, request.enabled))
    return curve.to_dict()


@app.delete("/api/curves/{curve_id}")
def remove_curve(curve_id: str) -> Dict[str, bool]:
    state.store.remove_curve(curve_id)
    return {"ok": True}


def controller_snapshots() -> List[Dict[str, object]]:
    controllers = state.hardware.list_controllers()
    for controller in controllers:
        identifier = str(controller["identifier"])
        name = state.store.get_controller_name(identifier)
        controller["name"] = name
        controller["display_name"] = name or controller["serial_number"]
        controller["fans"] = []
        controller["fan_error"] = None
        try:
            fan_rpm = state.hardware.get_all_fan_rpm(identifier)
            controller["fans"] = [
                {"id": str(fan), "label": f"Fan {fan}", "rpm": rpm}
                for fan, rpm in sorted(fan_rpm.items())
            ]
        except Exception as exc:
            controller["fan_error"] = str(exc)
    return controllers


def _or_http_error(callback):
    try:
        return callback()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Request failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def main() -> None:
    uvicorn.run(
        "controller_app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
