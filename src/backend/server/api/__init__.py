from backend.server.api import controls, devices, mappings, routines, runs, safety, session

ROUTERS = [
    session.router,
    controls.router,
    devices.router,
    mappings.router,
    routines.router,
    runs.router,
    safety.router,
]
