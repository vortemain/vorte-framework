try:
    try:
        from vorte._vorte_engine import VorteEngine as _NativeVorteEngine
    except ImportError:
        from _vorte_engine import VorteEngine as _NativeVorteEngine
except ImportError as e:
    raise RuntimeError(
        "Vorte Rust engine is required but the compiled extension _vorte_engine "
        "could not be imported. Please build it using `maturin develop --release`."
    ) from e


def _worker_target(script_path, module_name, app_name, h, p, worker_sock):
    import os
    import sys
    import importlib
    
    try:
        # Disable watchfiles/reloader inside child if any
        os.environ["VORTE_APP_DEBUG"] = "false"
    except Exception:
        pass
    
    # Detach the socket handle/descriptor (automatically duplicated by multiprocessing)
    fd = worker_sock.detach()
    
    # Re-import the application object in the child process
    sys.path.insert(0, os.path.dirname(os.path.abspath(script_path)))
    mod = importlib.import_module(module_name)
    app_obj = None
    if app_name:
        app_obj = getattr(mod, app_name, None)
    if app_obj is None:
        # Fallback: search for Vorte or FastAPI instances
        for k, v in vars(mod).items():
            if v.__class__.__name__ in ("Vorte", "FastAPI"):
                app_obj = v
                break
                
    if app_obj is None:
        raise RuntimeError(f"Could not load application from {module_name}")
        
    # Re-register routes for the child engine (so it matches routes correctly)
    from vorte.engine import VorteEngine as PyVorteEngine
    temp_engine = PyVorteEngine(app_obj)
    engine = temp_engine._engine
    
    engine.run(app_obj, h, p, 1, fd)


class VorteEngine:
    def __init__(self, app=None, *, host="0.0.0.0", port=8000, workers=1):
        self._app = app
        self._host = host
        self._port = port
        self._workers = workers
        self._engine = _NativeVorteEngine()
        
        if app is not None:
            self._register_routes(app)

    def _register_routes(self, app):
        actual_app = app
        if hasattr(app, 'fastapi'):
            actual_app = app.fastapi

        if hasattr(actual_app, 'routes'):
            from fastapi.routing import APIRoute
            for route in actual_app.routes:
                if isinstance(route, APIRoute):
                    for method in (route.methods or set()):
                        self._engine.add_route(method, route.path)

    def add_route(self, method: str, path: str):
        self._engine.add_route(method, path)
        return self

    def run(self, app=None, *, host=None, port=None, workers=None, sock_fd=None):
        target_app = app or self._app
        if target_app is None:
            raise ValueError("No application provided. Pass app to VorteEngine() or run().")

        # Initialize native Rust tracing
        try:
            from vorte._vorte_engine import init_logging
            from vorte.core.config import settings
            init_logging(settings.app_debug)
        except ImportError:
            pass

        run_host = host or self._host
        run_port = port or self._port
        run_workers = workers or self._workers

        actual_app = target_app
        if hasattr(target_app, 'fastapi'):
            pass

        if sock_fd is not None or run_workers <= 1:
            self._engine.run(actual_app, run_host, run_port, run_workers, sock_fd)
            return

        # Multi-process master-worker model
        import socket
        import multiprocessing
        import time
        import os

        # Bind master socket in the parent process
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((run_host, run_port))
        sock.listen(1024)
        sock.set_inheritable(True)

        print(f"  [Vorte Multi-Process] Master binding to {run_host}:{run_port}")
        print(f"  [Vorte Multi-Process] Spawning {run_workers} worker processes...")

        import sys
        script_path = os.path.abspath(sys.argv[0])
        module_name = os.path.splitext(os.path.basename(script_path))[0]
        
        # Try to find the app variable name in the main module
        app_name = "app"
        try:
            import __main__
            for k, v in vars(__main__).items():
                if v is actual_app:
                    app_name = k
                    break
        except Exception:
            pass

        processes = []

        try:
            for i in range(run_workers):
                p = multiprocessing.Process(
                    target=_worker_target,
                    args=(script_path, module_name, app_name, run_host, run_port, sock),
                )
                p.daemon = True
                p.start()
                processes.append(p)

            # Monitor worker processes
            while True:
                time.sleep(1.0)
                for i, p in enumerate(processes):
                    if not p.is_alive():
                        print(f"  [Vorte Multi-Process] Worker {i} died. Restarting...")
                        new_p = multiprocessing.Process(
                            target=_worker_target,
                            args=(script_path, module_name, app_name, run_host, run_port, sock),
                        )
                        new_p.daemon = True
                        new_p.start()
                        processes[i] = new_p
        except KeyboardInterrupt:
            print("  [Vorte Multi-Process] Shutting down worker processes...")
            for p in processes:
                p.terminate()
            for p in processes:
                p.join()
            sock.close()
            print("  [Vorte Multi-Process] Shutdown complete.")

    @property
    def is_native(self) -> bool:
        return True

    @property
    def route_count(self) -> int:
        return self._engine.route_count
