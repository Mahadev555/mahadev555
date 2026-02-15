```python
#!/usr/bin/python
# -*- coding: utf-8 -*-

class SoftwareEngineer:

    def __init__(self):
        self.name = "Mahadev M"
        self.role = "AI Engineer & Backend-Focused Developer"
        self.specialization = ["Agentic AI", "RAG Systems", "Automation"]
        self.languages = ["Python", "JavaScript", "SQL"]
        self.backend = ["FastAPI", "Node.js", "Microservices"]
        self.cloud = ["Azure AI", "Azure Cloud", "Docker 🐳"]
        self.databases = ["PostgreSQL", "Cosmos DB", "Vector DBs"]
        self.current_focus = ["AI Agents", "Search Pipelines", "Backend Systems"]

    def say_hi(self):
        print("Building AI agents & backend systems. Glad you’re here.")

me = SoftwareEngineer()
me.say_hi()
