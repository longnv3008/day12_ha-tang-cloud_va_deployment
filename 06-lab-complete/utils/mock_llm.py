"""
Mock LLM — no API key required.
Returns canned responses to simulate LLM latency and output.
Replace the `ask` call in main.py with a real client when ready.
"""
import time
import random


MOCK_RESPONSES = {
    "default": [
        "Day is AI agent (mock). In production, this would be a real OpenAI/Anthropic response.",
        "Agent is running! (mock response) Ask me anything.",
        "I am an AI agent deployed on the cloud. Your question has been received.",
    ],
    "docker": [
        "Docker is a tool for packaging apps into containers so they run consistently everywhere. "
        "Build once, run anywhere!"
    ],
    "deploy": [
        "Deployment is the process of moving code from your machine to a server so others can use it."
    ],
    "health": ["Agent is healthy. All systems operational."],
    "kubernetes": [
        "Kubernetes (K8s) is a container orchestration platform that automates deployment, "
        "scaling, and management of containerized applications."
    ],
    "redis": [
        "Redis is an in-memory data store used for caching, session storage, and pub/sub messaging. "
        "It enables stateless microservices by externalising shared state."
    ],
    "scale": [
        "Horizontal scaling adds more instances; vertical scaling adds more resources to one instance. "
        "Stateless services scale horizontally behind a load balancer."
    ],
    "rate limit": [
        "Rate limiting protects your API from abuse by capping requests per user per time window. "
        "Common algorithms: token bucket, sliding window counter."
    ],
}


def ask(question: str, delay: float = 0.1) -> str:
    """
    Mock LLM call with simulated latency.
    Keyword-matches the question; falls back to a default response.
    """
    time.sleep(delay + random.uniform(0, 0.05))

    q_lower = question.lower()
    for keyword, responses in MOCK_RESPONSES.items():
        if keyword in q_lower:
            return random.choice(responses)

    return random.choice(MOCK_RESPONSES["default"])


def ask_stream(question: str):
    """
    Mock streaming response — yield one word at a time.
    """
    response = ask(question)
    for word in response.split():
        time.sleep(0.04)
        yield word + " "
