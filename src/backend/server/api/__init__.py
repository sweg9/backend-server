from backend.server.api import controls, demo, devices, mappings, routines, runs, safety, session, situations

ROUTERS = [
    session.router,
    controls.router,
    devices.router,
    mappings.router,
    routines.router,
    runs.router,
    safety.router,
    situations.router,
    demo.router,
]
