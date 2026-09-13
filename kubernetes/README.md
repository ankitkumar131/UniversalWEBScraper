# Kubernetes deployment

Manifests for horizontally scaling the Universal Web Scraper per the sd.txt
"Scaling Strategy": stateless API + worker pods behind shared queue/storage,
HPA on queue depth / CPU, config/secrets split.

```bash
kubectl apply -f kubernetes/configmap.yaml
kubectl apply -f kubernetes/secrets.yaml        # edit first!
kubectl apply -f kubernetes/api-deployment.yaml
kubectl apply -f kubernetes/worker-deployment.yaml
kubectl apply -f kubernetes/hpa.yaml
```

Requirements: a PostgreSQL (or any DATABASE_URL) reachable from the cluster,
and a Redis for the Celery broker + shared rate limiting. The worker image
carries a full Playwright Chromium (~2 GB image, ~2-4 GB RAM at runtime);
size worker pods accordingly (see resource limits in the manifests).
