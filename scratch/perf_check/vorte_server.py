from vorte import Vorte
app = Vorte(auto_load=False)

@app.get("/api/v1/hello")
async def hello():
    return {"message": "Welcome to Vorte!"}

if __name__ == "__main__":
    from vorte.engine import VorteEngine
    engine = VorteEngine(app)
    engine.run(host="127.0.0.1", port=8000, workers=1)
