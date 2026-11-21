import os

# Testerna styr själva vilken LLM som används; inspelade svar ska inte slå på
# bara för att ingen API-nyckel finns i testmiljön.
os.environ.setdefault("DEMO_REPLAY", "0")
