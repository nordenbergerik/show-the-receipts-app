import os

# Testerna styr själva vilken LLM som används; inspelade svar ska inte slå på
# bara för att ingen API-nyckel finns i testmiljön.
os.environ.setdefault("DEMO_REPLAY", "0")

# Inloggningen testas för sig (test_auth.py); övriga tester körs utan den, även
# om en lokal .env har AUTH_PASSWORD satt.
os.environ["AUTH_PASSWORD"] = ""
