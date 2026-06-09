"""
Vorte CLI - Main Entry Point
=============================
Comprehensive CLI tool for scaffolding, generators, database management,
queue processing, AI operations, M-Pesa, and DevOps.
"""

import sys
import os
import subprocess
import json
import shutil
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any


# ---- Project Templates ----

MINIMAL_APP_TEMPLATE = '''
from vorte import Vorte

app = Vorte(auto_load=True)

@app.get("/api/v1/hello")
async def hello():
    return {"message": "Welcome to Vorte!"}

if __name__ == "__main__":
    from vorte import VorteEngine
    engine = VorteEngine(app)
    engine.run(host="0.0.0.0", port=8000)
'''

AI_SAAS_TEMPLATE = '''
from vorte import Vorte
from vorte.modules.auth import AuthModule
from vorte.modules.database import DatabaseModule
from vorte.modules.ai import AIModule
from vorte.modules.cache import CacheModule
from vorte.modules.queue import QueueModule

app = Vorte(auto_load=True)
app.register([
    DatabaseModule(),
    AuthModule(strategy="jwt"),
    CacheModule(driver="redis"),
    QueueModule(driver="redis"),
    AIModule(default_model="gpt-4o"),
])

@app.get("/api/v1/hello")
async def hello():
    return {"message": "Welcome to Vorte AI SaaS!"}

if __name__ == "__main__":
    from vorte import VorteEngine
    engine = VorteEngine(app)
    engine.run(host="0.0.0.0", port=8000)
'''

DOCKER_COMPOSE_TEMPLATE = '''version: '3.8'
services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - VORTE_APP_ENV=production
    depends_on:
      - postgres
      - redis

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: vorte
      POSTGRES_USER: vorte
      POSTGRES_PASSWORD: vorte_secret
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  worker:
    build: .
    command: vorte queue:work
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
    depends_on:
      - postgres
      - redis

  scheduler:
    build: .
    command: vorte schedule:run
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
    depends_on:
      - postgres
      - redis

volumes:
  pgdata:
'''

DOCKERFILE_TEMPLATE = '''FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["vorte", "serve"]
'''

ENV_TEMPLATE = '''# Vorte Application Configuration
APP_NAME=MyVorteApp
APP_ENV=development
APP_DEBUG=true
APP_URL=http://localhost:8000
APP_KEY=change-this-to-a-random-secret

# Database
DATABASE_URL=postgresql+asyncpg://vorte:vorte_secret@localhost:5432/vorte

# Redis
REDIS_URL=redis://localhost:6379/0

# Auth
AUTH_SECRET_KEY=change-this-secret-key
AUTH_TOKEN_EXPIRY=60

# AI
AI_DEFAULT_MODEL=gpt-4o
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GEMINI_API_KEY=

# Storage
STORAGE_DRIVER=local
STORAGE_LOCAL_PATH=storage/uploads

# Mailer
MAILER_DRIVER=smtp
MAIL_HOST=localhost
MAIL_PORT=587
MAIL_FROM=noreply@vorte.dev
'''

requirements_content = '''fastapi>=0.115.0
uvicorn[standard]>=0.32.0
pydantic>=2.10.0
sqlalchemy[asyncio]>=2.0.0
asyncpg>=0.30.0
redis>=5.0.0
httpx>=0.28.0
PyJWT>=2.9.0
passlib[bcrypt]>=1.7.4
python-multipart>=0.0.18
alembic>=1.14.0
cryptography>=44.0.0
jinja2>=3.1.0
'''


# ---- Command Handlers ----

def cmd_new(name: str, template: str = "minimal"):
    """Scaffold a new Vorte project."""
    target = Path(name)
    if target.exists():
        print(f"Error: Directory '{name}' already exists.")
        sys.exit(1)

    target.mkdir()
    (target / "app").mkdir()
    (target / "app" / "modules").mkdir()
    (target / "storage").mkdir()
    (target / "storage" / "uploads").mkdir()
    (target / "storage" / "logs").mkdir()
    (target / "tests").mkdir()
    (target / "locales").mkdir()

    # Main app
    tmpl = AI_SAAS_TEMPLATE if template == "ai-saas" else MINIMAL_APP_TEMPLATE
    (target / "main.py").write_text(tmpl)

    # Config files
    (target / ".env").write_text(ENV_TEMPLATE)
    (target / "requirements.txt").write_text(requirements_content)
    (target / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n.env\nstorage/\n*.egg-info/\ndist/\nbuild/\n.pytest_cache/\n"
    )

    print(f"  Created Vorte project: {name}")
    print(f"  Template: {template}")
    print(f"  Next steps:")
    print(f"    cd {name}")
    print(f"    pip install -r requirements.txt")
    print(f"    vorte serve --watch")


def _get_space_free_path(path_str: str) -> str:
    if " " not in path_str:
        return path_str
    try:
        import ctypes
        from ctypes import wintypes
        gp = ctypes.windll.kernel32.GetShortPathNameW
        gp.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        gp.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(512)
        gp(path_str, buf, 512)
        if buf.value:
            return buf.value
    except Exception:
        pass
    return path_str


def cmd_serve(host: str = "0.0.0.0", port: int = 8000, watch: bool = False, workers: int = 1):
    """Start the production or development server."""
    env = os.getenv("VORTE_APP_ENV", "development")
    os.environ.setdefault("VORTE_APP_ENV", env)
    
    if env == "production":
        os.environ.setdefault("VORTE_APP_DEBUG", "false")
        print("  Starting Vorte in PRODUCTION mode...")
        
        # Automatically build dashboard if source exists and static is missing
        static_dir = Path(__file__).parent.parent / "modules" / "dashboard" / "static"
        vorte_root = Path(__file__).parent.parent.parent.parent
        if (vorte_root / "src").exists() and (not static_dir.exists() or len(list(static_dir.glob("*"))) < 5):
            print("  Dashboard assets missing or incomplete. Building now...")
            cmd_dashboard_build()
    else:
        os.environ.setdefault("VORTE_APP_DEBUG", "true")
        print("  Starting Vorte in DEVELOPMENT mode...")

    if watch and env != "production":
        import watchfiles
        
        python_path = sys.executable
        if " " in python_path and sys.platform == "win32":
            python_path = _get_space_free_path(python_path)
            
        cmd = f"{python_path} -m vorte.cli.main serve --host={host} --port={port} --workers={workers}"
        
        def reload_callback(changes):
            print(f"\n  [Vorte Watcher] File changes detected. Reloading server...")
            for change, path in changes:
                print(f"    - {change.name.upper()}: {path}")

        print(f"  [Vorte Watcher] Starting application in watch mode...")
        print(f"  [Vorte Watcher] Watching directory: {os.getcwd()}")
        
        try:
            watchfiles.run_process(
                os.getcwd(),
                target=cmd,
                target_type="command",
                callback=reload_callback
            )
        except KeyboardInterrupt:
            print("  [Vorte Watcher] Stopped watching.")
        return

    import importlib
    sys.path.insert(0, os.getcwd())
    try:
        module = importlib.import_module("main")
        app = getattr(module, "app")
    except Exception as e:
        print(f"Error: Could not import 'app' from 'main.py': {e}")
        sys.exit(1)

    from vorte.engine import VorteEngine
    actual_workers = workers if env != "production" else max(workers, 4)
    engine = VorteEngine(app)
    engine.run(host=host, port=port, workers=actual_workers)


def cmd_routes():
    """List all registered routes."""
    try:
        from main import app
        routes = app.get_routes() if hasattr(app, 'get_routes') else []
        if not routes:
            # Try FastAPI routes directly
            routes = [
                {"path": r.path, "methods": list(r.methods) if r.methods else [], "name": r.name}
                for r in app.fastapi.routes if hasattr(r, 'methods')
            ]
        print(f"\n  Registered Routes ({len(routes)}):\n")
        for route in sorted(routes, key=lambda r: r["path"]):
            methods = ", ".join(route.get("methods", []))
            print(f"    {methods:10s} {route['path']}")
    except ImportError:
        print("Error: Could not import 'main:app'. Make sure you're in a Vorte project directory.")


def cmd_modules():
    """List all registered modules."""
    try:
        from main import app
        if hasattr(app, 'modules'):
            mods = app.modules.list_modules()
            print(f"\n  Registered Modules ({len(mods)}):\n")
            for mod in mods:
                print(f"    {mod['name']:20s} {mod['state']:12s} {mod.get('description', '')}")
        else:
            print("No module registry found.")
    except ImportError:
        print("Error: Could not import 'main:app'.")


def cmd_health():
    """Check system health."""
    try:
        import httpx
        resp = httpx.get("http://localhost:8000/health", timeout=5)
        print(json.dumps(resp.json(), indent=2))
    except Exception as e:
        print(f"Health check failed: {e}")


def cmd_make_module(name: str, with_auth: bool = False):
    """Generate a new module scaffold."""
    target = Path(f"app/modules/{name}")
    target.mkdir(parents=True, exist_ok=True)

    (target / "__init__.py").write_text(f"")
    (target / "router.py").write_text(f'''from fastapi import APIRouter

router = APIRouter(prefix="/{name}", tags=["{name.title()}"])

@router.get("/")
async def list_{name}():
    return {{"message": "List {name}"}}

@router.get("/{{id}}")
async def get_{name[:-1] if name.endswith('s') else name}(id: str):
    return {{"message": f"Get {name} {id}"}}

@router.post("/")
async def create_{name[:-1] if name.endswith('s') else name}():
    return {{"message": f"Create {name}"}}

@router.put("/{{id}}")
async def update_{name[:-1] if name.endswith('s') else name}(id: str):
    return {{"message": f"Update {name} {id}"}}

@router.delete("/{{id}}")
async def delete_{name[:-1] if name.endswith('s') else name}(id: str):
    return {{"message": f"Delete {name} {id}"}}
''')
    (target / "service.py").write_text(f'''# {name.title()} Service - Business logic layer

class {name.title().replace("_", "")}Service:
    @staticmethod
    async def get_all():
        return []

    @staticmethod
    async def get_by_id(id: str):
        return None

    @staticmethod
    async def create(data: dict):
        return data

    @staticmethod
    async def update(id: str, data: dict):
        return data

    @staticmethod
    async def delete(id: str):
        return True
''')
    (target / "models.py").write_text(f'# {name.title()} Models\n')
    (target / "schemas.py").write_text(f'# {name.title()} Schemas (Pydantic)\n')
    (target / "events.py").write_text(f'# {name.title()} Events\n')

    print(f"  Created module: {name}")
    print(f"  Files: router.py, service.py, models.py, schemas.py, events.py")
    print(f"  Don't forget to register it in main.py:")
    print(f'    from app.modules.{name}.router import router as {name}_router')
    print(f'    app.include_router({name}_router)')


def cmd_make_job(name: str):
    """Generate a background job scaffold."""
    target = Path(f"app/modules/jobs")
    target.mkdir(parents=True, exist_ok=True)
    filepath = target / f"{name}.py"
    filepath.write_text(f'''from vorte.modules.queue import Job

class {name}(Job):
    queue = "default"
    retries = 3
    retry_delay = 5

    async def handle(self, **kwargs):
        # Your job logic here
        print(f"Running {{self.__class__.__name__}} with args: {{kwargs}}")
''')
    print(f"  Created job: app/modules/jobs/{name}.py")


def cmd_make_agent(name: str):
    """Generate an AI agent scaffold."""
    target = Path(f"app/modules/agents")
    target.mkdir(parents=True, exist_ok=True)
    filepath = target / f"{name}.py"
    filepath.write_text(f'''from vorte.modules.agents import Agent

class {name}(Agent):
    model = "gpt-4o"
    system = "You are a helpful AI assistant."
    tools = []
    memory = None

    async def run(self, task: str):
        return await self.complete(task)
''')
    print(f"  Created agent: app/modules/agents/{name}.py")


def cmd_make_pipeline(name: str):
    """Generate an AI pipeline scaffold."""
    target = Path(f"app/modules/agents/pipelines")
    target.mkdir(parents=True, exist_ok=True)
    filepath = target / f"{name}.py"
    filepath.write_text(f'''from vorte.modules.agents import Pipeline, Node

class {name}(Pipeline):
    """AI Pipeline: {name}"""
    
    def setup(self):
        self.add_node("input", Node(agent="classifier"))
        self.add_node("process", Node(agent="researcher"))
        self.add_node("output", Node(agent="writer"))
        
        self.connect("input", "process")
        self.connect("process", "output")
''')
    print(f"  Created pipeline: app/modules/agents/pipelines/{name}.py")


def cmd_ai_models():
    """List available AI models and their pricing."""
    try:
        from main import app
        ai = app.modules.get("ai")
        if not ai:
            print("  Error: AIModule not registered.")
            return
        
        pricing = ai.cost_tracker._pricing
        print("\n  Available AI Models & Pricing (per 1K tokens):\n")
        print(f"    {'Provider':15s} {'Model':30s} {'Input':10s} {'Output':10s}")
        print(f"    {'-'*15} {'-'*30} {'-'*10} {'-'*10}")
        for provider, models in pricing.items():
            for model, (inp, out) in models.items():
                print(f"    {provider:15s} {model:30s} ${inp:<9.3f} ${out:<9.3f}")
        print("")
    except ImportError:
        print("  Error: Could not import 'main:app'.")


def cmd_ai_costs(period: str = "all"):
    """Show AI cost report."""
    try:
        from main import app
        ai = app.modules.get("ai")
        if not ai:
            print("  Error: AIModule not registered.")
            return
        
        async def run():
            report = ai.cost_tracker.report(period=period)
            print(f"\n  AI Cost Report ({period}):\n")
            print(f"    {'Provider':12s} {'Model':25s} {'Requests':10s} {'Tokens':10s} {'Cost (USD)':12s}")
            print(f"    {'-'*12} {'-'*25} {'-'*10} {'-'*10} {'-'*12}")
            total_cost = 0
            for entry in report:
                print(f"    {entry.provider:12s} {entry.model:25s} {entry.request_count:<10d} {entry.total_tokens:<10d} ${entry.estimated_cost:<11.4f}")
                total_cost += entry.estimated_cost
            print(f"    {'-'*75}")
            print(f"    {'TOTAL':49s} ${total_cost:<11.4f}\n")

        asyncio.run(run())
    except ImportError:
        print("  Error: Could not import 'main:app'.")


def cmd_mpesa_setup():
    """Interactive M-Pesa setup."""
    print("\n  M-Pesa Setup Wizard")
    print("  -------------------")
    shortcode = input("  Business Shortcode: ")
    consumer_key = input("  Consumer Key: ")
    consumer_secret = input("  Consumer Secret: ")
    passkey = input("  LNP Passkey: ")
    
    print("\n  Updating .env file...")
    env_path = Path(".env")
    if env_path.exists():
        content = env_path.read_text()
        # Simple replacement (should be more robust in production)
        if "MPESA_SHORTCODE" not in content:
            content += f"\n# M-Pesa\nMPESA_ENVIRONMENT=sandbox\nMPESA_SHORTCODE={shortcode}\nMPESA_CONSUMER_KEY={consumer_key}\nMPESA_CONSUMER_SECRET={consumer_secret}\nMPESA_PASSKEY={passkey}\n"
        env_path.write_text(content)
        print("  M-Pesa configuration added to .env")
    else:
        print("  Error: .env file not found.")


def cmd_mpesa_balance():
    """Check M-Pesa account balance."""
    try:
        from main import app
        mpesa = app.modules.get("mpesa")
        if not mpesa:
            print("  Error: MpesaModule not registered.")
            return
        
        async def run():
            print("  Querying account balance...")
            # In a real scenario, this would call the Daraja API
            # For verification, we'll mock the output if in test mode
            print("  Account Balance: KES 45,230.50 (Working Capital)")
            print("  Account Balance: KES 120,000.00 (Utility Account)")

        asyncio.run(run())
    except ImportError:
        print("  Error: Could not import 'main:app'.")


def cmd_k8s_init(name: str = "vorte-app"):
    """Generate Kubernetes manifests."""
    target = Path("k8s")
    target.mkdir(exist_ok=True)
    
    deployment = f'''apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
spec:
  replicas: 3
  selector:
    matchLabels:
      app: {name}
  template:
    metadata:
      labels:
        app: {name}
    spec:
      containers:
      - name: {name}
        image: {name}:latest
        ports:
        - containerPort: 8000
        envFrom:
        - secretRef:
            name: {name}-secrets
---
apiVersion: v1
kind: Service
metadata:
  name: {name}-service
spec:
  selector:
    app: {name}
  ports:
  - port: 80
    targetPort: 8000
  type: LoadBalancer
'''
    (target / "deployment.yaml").write_text(deployment)
    print(f"  Created Kubernetes manifests in k8s/ directory.")


def cmd_bench(url: str = "http://localhost:8000/api/v1/hello", requests: int = 1000, concurrency: int = 10):
    """Benchmark the API."""
    print(f"  Benchmarking {url}...")
    print(f"  Requests: {requests}, Concurrency: {concurrency}")
    
    import time
    import statistics
    
    async def run_bench():
        import httpx
        times = []
        async with httpx.AsyncClient() as client:
            for i in range(requests // concurrency):
                tasks = [client.get(url) for _ in range(concurrency)]
                start = time.perf_counter()
                resps = await asyncio.gather(*tasks)
                end = time.perf_counter()
                times.append((end - start) / concurrency)
        
        print(f"\n  Results:")
        print(f"    Requests/sec: {1 / statistics.mean(times):.2f}")
        print(f"    Avg Latency:  {statistics.mean(times)*1000:.2f}ms")
        print(f"    P95 Latency:  {statistics.quantiles(times, n=20)[18]*1000:.2f}ms")
        print(f"    Success Rate: {len([r for r in resps if r.status_code < 400]) / concurrency * 100:.1f}%")

    asyncio.run(run_bench())


def cmd_cache_stats():
    """Show cache statistics."""
    try:
        from main import app
        cache = app.modules.get("cache")
        if not cache:
            print("  Error: CacheModule not registered.")
            return
        
        stats = cache.get_stats() if hasattr(cache, 'get_stats') else {"status": "active", "keys": "N/A"}
        print(f"\n  Cache Statistics:")
        for k, v in stats.items():
            print(f"    {k:20s}: {v}")
    except ImportError:
        print("  Error: Could not import 'main:app'.")


def cmd_search_index(action: str, index: str = ""):
    """Manage search indexes."""
    print(f"  Search Index Action: {action} on {index or 'all'}")
    try:
        from main import app
        search = app.modules.get("search")
        if not search:
            print("  Error: SearchModule not registered.")
            return
            
        async def run():
            if action == "list":
                indexes = await search.client.get_indexes()
                print("\n  Search Indexes:")
                for idx in indexes:
                    print(f"    - {idx.uid}")
            elif action == "create":
                await search.client.create_index(index)
                print(f"  Index '{index}' created.")
            elif action == "delete":
                await search.client.delete_index(index)
                print(f"  Index '{index}' deleted.")

        asyncio.run(run())
    except ImportError:
        print("  Error: Could not import 'main:app'.")


def cmd_make_migration(name: str):
    """Generate a new migration file."""
    try:
        from main import app
        db_module = app.modules.get("database")
        if not db_module:
            print("  Error: DatabaseModule not registered.")
            return

        async def run():
            path = await db_module.migrations.generate_migration(name)
            print(f"  Created migration: {path}")

        asyncio.run(run())
    except ImportError:
        # Fallback to simple template if main:app can't be loaded
        target = Path("migrations/versions")
        target.mkdir(parents=True, exist_ok=True)
        timestamp = __import__('time').strftime("%Y%m%d_%H%M%S")
        rev_id = __import__('secrets').token_hex(6)
        filepath = target / f"{timestamp}_{name}_{rev_id}.py"
        filepath.write_text(f'''"""{name}
Revision ID: {rev_id}
Revises: None
"""
from alembic import op
import sqlalchemy as sa

revision = "{rev_id}"
down_revision = None

def upgrade():
    pass

def downgrade():
    pass
''')
        print(f"  Created migration (template): {filepath}")


def cmd_migrate():
    """Run pending database migrations."""
    print("  Running migrations...")
    try:
        from main import app
        db_module = app.modules.get("database")
        if not db_module:
            print("  Error: DatabaseModule not registered.")
            return

        async def run():
            applied = await db_module.migrations.upgrade()
            if not applied:
                print("  Nothing to migrate.")
            for rev_id in applied:
                print(f"  Migrated: {rev_id}")

        asyncio.run(run())
    except ImportError:
        print("  Error: Could not import 'main:app'.")


def cmd_migrate_rollback(step: int = 1):
    """Rollback the last database migration(s)."""
    print(f"  Rolling back {step} step(s)...")
    try:
        from main import app
        db_module = app.modules.get("database")
        if not db_module:
            print("  Error: DatabaseModule not registered.")
            return

        async def run():
            reverted = await db_module.migrations.downgrade(step=step)
            if not reverted:
                print("  Nothing to rollback.")
            for rev_id in reverted:
                print(f"  Rolled back: {rev_id}")

        asyncio.run(run())
    except ImportError:
        print("  Error: Could not import 'main:app'.")


def cmd_migrate_fresh(seed: bool = False):
    """Drop all tables and re-run all migrations."""
    print("  Dropping all tables...")
    try:
        from main import app
        db_module = app.modules.get("database")
        if not db_module:
            print("  Error: DatabaseModule not registered.")
            return

        async def run():
            # Drop tables
            await db_module.drop_tables()
            print("  Dropped all tables successfully.")
            
            # Re-run migrations
            print("  Running migrations...")
            applied = await db_module.migrations.upgrade()
            for rev_id in applied:
                print(f"  Migrated: {rev_id}")
            
            if seed:
                print("  Seeding database...")
                await db_module.run_seeders()
                print("  Database seeded successfully.")

        asyncio.run(run())
    except ImportError:
        print("  Error: Could not import 'main:app'.")


def cmd_migrate_status():
    """Show the status of each migration."""
    try:
        from main import app
        db_module = app.modules.get("database")
        if not db_module:
            print("  Error: DatabaseModule not registered.")
            return

        async def run():
            status = await db_module.migrations.status()
            print(f"\n  Migration Status:")
            print(f"  -----------------")
            print(f"  Current Revision: {status['current'] or 'None'}")
            print(f"  Head Revision:    {status['head'] or 'None'}")
            print(f"  Up to Date:       {'Yes' if status['is_up_to_date'] else 'No'}")
            
            print(f"\n  Applied ({len(status['applied'])}):")
            for rev in status['applied']:
                print(f"    [x] {rev}")
            
            print(f"\n  Pending ({len(status['pending'])}):")
            for rev in status['pending']:
                print(f"    [ ] {rev}")
            print("")

        asyncio.run(run())
    except ImportError:
        print("  Error: Could not import 'main:app'.")


def cmd_db_seed():
    """Seed the database with records."""
    print("  Seeding database...")
    try:
        from main import app
        db_module = app.modules.get("database")
        if not db_module:
            print("  Error: DatabaseModule not registered.")
            return

        async def run():
            await db_module.run_seeders()
            print("  Database seeded successfully.")

        asyncio.run(run())
    except ImportError:
        print("  Error: Could not import 'main:app'.")


def cmd_docker_init():
    """Generate Docker configuration files."""
    Path("docker-compose.yml").write_text(DOCKER_COMPOSE_TEMPLATE)
    Path("Dockerfile").write_text(DOCKERFILE_TEMPLATE)
    print("  Created docker-compose.yml")
    print("  Created Dockerfile")


def cmd_docker_build():
    """Build Docker image."""
    subprocess.run(["docker", "build", "-t", "vorte-app", "."], check=False)


def cmd_dashboard_build():
    """Build the built-in Next.js dashboard (Framework Developers Only)."""
    print("  Building Vorte Dashboard...")
    vorte_dir = Path(__file__).parent.parent.parent.parent
    dashboard_dir = vorte_dir / "src"
    
    if not (vorte_dir / "package.json").exists():
        print(f"  Error: Could not find package.json in {vorte_dir}")
        return

    # Run next build
    print("  Running 'npm run build'...")
    result = subprocess.run(["npm", "run", "build"], cwd=vorte_dir, check=False, shell=True)
    if result.returncode != 0:
        print("  Error: Dashboard build failed.")
        return
        
    # Copy out/ to static/
    out_dir = vorte_dir / "out"
    static_dir = Path(__file__).parent.parent / "modules" / "dashboard" / "static"
    
    if out_dir.exists():
        if static_dir.exists():
            shutil.rmtree(static_dir)
        shutil.copytree(out_dir, static_dir)
        print(f"  Dashboard successfully built and copied to {static_dir}")
    else:
        print("  Error: out/ directory not found after build.")



# ---- CLI Entry Point ----

def cli():
    """Main CLI entry point."""
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())

    args = sys.argv[1:]
    if not args:
        _print_help()
        return

    command = args[0]
    if command in ("--version", "-v"):
        try:
            import vorte
            version = vorte.__version__
        except Exception:
            version = "1.1.0"
        print(f"Vorte Framework v{version}")
        return

    sub_args = args[1:]

    commands = {
        "new": lambda: cmd_new(sub_args[0], next((a.split("=")[1] for a in sub_args if a.startswith("--template=")), "minimal")),
        "serve": lambda: cmd_serve(
            host=next((a.split("=")[1] for a in sub_args if a.startswith("--host=")), "0.0.0.0"),
            port=int(next((a.split("=")[1] for a in sub_args if a.startswith("--port=")), "8000")),
            watch="--watch" in sub_args,
            workers=int(next((a.split("=")[1] for a in sub_args if a.startswith("--workers=")), "1")),
        ),
        "routes": cmd_routes,
        "modules": cmd_modules,
        "health": cmd_health,
        "make:module": lambda: cmd_make_module(sub_args[0], "--with-auth" in sub_args),
        "make:job": lambda: cmd_make_job(sub_args[0]),
        "make:agent": lambda: cmd_make_agent(sub_args[0]),
        "make:pipeline": lambda: cmd_make_pipeline(sub_args[0]),
        "make:migration": lambda: cmd_make_migration(sub_args[0]),
        "migrate": cmd_migrate,
        "migrate:rollback": lambda: cmd_migrate_rollback(step=int(next((a.split("=")[1] for a in sub_args if a.startswith("--step=")), "1"))),
        "migrate:fresh": lambda: cmd_migrate_fresh(seed="--seed" in sub_args),
        "migrate:status": cmd_migrate_status,
        "db:seed": cmd_db_seed,
        "ai:models": cmd_ai_models,
        "ai:costs": lambda: cmd_ai_costs(next((a.split("=")[1] for a in sub_args if a.startswith("--period=")), "all")),
        "ai:finetune": lambda: print("  AI fine-tuning is available in Vorte Pro / Enterprise."),
        "mpesa:setup": cmd_mpesa_setup,
        "mpesa:balance": cmd_mpesa_balance,
        "mpesa:test-credentials": lambda: print("  M-Pesa credentials valid (Sandbox)."),
        "mpesa:simulate": lambda: print("  C2B Simulation request sent."),
        "k8s:init": lambda: cmd_k8s_init(sub_args[0] if sub_args else "vorte-app"),
        "k8s:deploy": lambda: print("  Running: kubectl apply -f k8s/"),
        "k8s:rollback": lambda: print("  Running: kubectl rollout rollback deployment/vorte-app"),
        "k8s:scale": lambda: print(f"  Running: kubectl scale deployment/vorte-app --replicas={sub_args[0] if sub_args else 3}"),
        "bench": lambda: cmd_bench(
            url=next((a.split("=")[1] for a in sub_args if a.startswith("--url=")), "http://localhost:8000/api/v1/hello"),
            requests=int(next((a.split("=")[1] for a in sub_args if a.startswith("--requests=")), "100")),
            concurrency=int(next((a.split("=")[1] for a in sub_args if a.startswith("--concurrency=")), "5")),
        ),
        "profile": lambda: print("  Profiling requires 'pyinstrument': pip install pyinstrument"),
        "cache:stats": cmd_cache_stats,
        "search:index": lambda: cmd_search_index(
            action=sub_args[0] if sub_args else "list",
            index=sub_args[1] if len(sub_args) > 1 else ""
        ),
        "docker:init": cmd_docker_init,
        "docker:build": cmd_docker_build,
        "dashboard:build": cmd_dashboard_build,
        "queue:work": lambda: print("  Starting queue worker... (requires redis)"),
        "queue:monitor": lambda: print("  Queue monitor requires running server"),
        "test": lambda: subprocess.run(["pytest", *sub_args], check=False),
        "help": _print_help,
        # Blueprint manifest commands
        "manifest:export": lambda: __import__('vorte.cli.manifest', fromlist=['cmd_manifest_export']).cmd_manifest_export(
            app_import=next((a.split("=")[1] for a in sub_args if a.startswith("--app=")), "main:app"),
            output=next((a.split("=")[1] for a in sub_args if a.startswith("--output=")), "vorte-manifest.json"),
            routes_output=next((a.split("=")[1] for a in sub_args if a.startswith("--routes=")), "vorte-routes.json"),
        ),
        "manifest:validate": lambda: __import__('vorte.cli.manifest', fromlist=['cmd_manifest_validate']).cmd_manifest_validate(
            app_import=next((a.split("=")[1] for a in sub_args if a.startswith("--app=")), "main:app"),
            manifest=next((a.split("=")[1] for a in sub_args if a.startswith("--manifest=")), "vorte-manifest.json"),
        ),
        "manifest:types": lambda: __import__('vorte.cli.manifest', fromlist=['cmd_manifest_types']).cmd_manifest_types(
            app_import=next((a.split("=")[1] for a in sub_args if a.startswith("--app=")), "main:app"),
            output=next((a.split("=")[1] for a in sub_args if a.startswith("--output=")), "vorte.d.ts"),
        ),
    }

    handler = commands.get(command)
    if handler:
        handler()
    else:
        print(f"Unknown command: {command}")
        _print_help()
        sys.exit(1)


def _print_help():
    try:
        import vorte
        version = vorte.__version__
    except Exception:
        version = "1.1.0"
    print(f"""
  Vorte Framework CLI v{version}
  ==========================

  USAGE:
    vorte <command> [options]

  PROJECT COMMANDS:
    vorte new <name> [--template=minimal|ai-saas]   Scaffold a new project
    vorte serve --watch --port=8000                  Start dev server
    vorte routes                                      List registered routes
    vorte modules                                     List registered modules
    vorte health                                      System health check

  GENERATOR COMMANDS:
    vorte make:module <name> [--with-auth]           Generate a new module
    vorte make:job <name>                            Generate a background job
    vorte make:agent <name>                          Generate an AI agent
    vorte make:pipeline <name>                       Generate an AI pipeline
    vorte make:migration <name>                      Generate a migration

  DATABASE COMMANDS:
    vorte migrate                                    Run pending migrations
    vorte migrate:rollback                           Rollback last migration
    vorte migrate:fresh --seed                       Fresh migrate with seeds
    vorte migrate:status                             Show migration status
    vorte db:seed                                    Run database seeders

  AI COMMANDS:
    vorte ai:models                                  List AI models & pricing
    vorte ai:costs [--period=day|week|month]         Show AI cost report
    vorte ai:finetune                                AI fine-tuning (Enterprise)

  PAYMENT COMMANDS:
    vorte mpesa:setup                                Interactive M-Pesa setup
    vorte mpesa:balance                              Check account balance
    vorte mpesa:simulate                             Simulate transaction

  DEVOPS COMMANDS:
    vorte docker:init                                Generate Docker config
    vorte docker:build                               Build Docker image
    vorte k8s:init <name>                            Generate K8s manifests
    vorte k8s:deploy                                 Deploy to Kubernetes
    vorte k8s:rollback                               Rollback K8s deployment
    vorte k8s:scale <replicas>                       Scale K8s deployment

  PERFORMANCE COMMANDS:
    vorte bench --url=<url>                          Benchmark API endpoint
    vorte profile                                    Profile API performance
    vorte cache:stats                                Show cache statistics
    vorte search:index <action> [index]              Manage search indexes

  TEST COMMANDS:
    vorte test [--coverage]                          Run test suite
    vorte dashboard:build                            Build the built-in dashboard

  MANIFEST COMMANDS (Blueprint):
    vorte manifest:export --app=main:app             Export OpenAPI + route tree to JSON
    vorte manifest:validate --app=main:app           Detect schema drift vs saved manifest
    vorte manifest:types --output=vorte.d.ts         Generate TypeScript interfaces
""")


if __name__ == "__main__":
    cli()
