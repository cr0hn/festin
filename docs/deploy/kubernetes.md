# Kubernetes deployment

Manifests for a single-replica dashboard. [HA patterns](ha.md) build on this — read the constraints there before scaling.

## Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: festin
  labels: {app: festin}
spec:
  replicas: 1                    # see HA page before raising this
  strategy: {type: Recreate}     # SQLite volume can't be shared by two pods
  selector:
    matchLabels: {app: festin}
  template:
    metadata:
      labels: {app: festin}
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 999
        fsGroup: 999
      containers:
        - name: festin
          image: ghcr.io/cr0hn/festin:latest
          args: ["serve", "--host", "0.0.0.0", "--db", "/data/festin.db"]
          env:
            - name: FESTIN_JWT_SECRET
              valueFrom:
                secretKeyRef: {name: festin-secrets, key: jwt-secret}
          ports:
            - containerPort: 8420
              name: http
          volumeMounts:
            - name: data
              mountPath: /data
          resources:
            requests: {cpu: 100m, memory: 128Mi}
            limits: {cpu: "1", memory: 512Mi}
          readinessProbe:
            httpGet: {path: /api/v1/health, port: 8420}
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet: {path: /api/v1/health, port: 8420}
            initialDelaySeconds: 15
            periodSeconds: 20
            failureThreshold: 3
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: festin-data
```

## Secret

```bash
kubectl create secret generic festin-secrets \
  --from-literal=jwt-secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

## PVC

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: festin-data
spec:
  accessModes: ["ReadWriteOnce"]   # single-node attach — matches SQLite
  resources:
    requests:
      storage: 5Gi
```

## Service + Ingress

```yaml
apiVersion: v1
kind: Service
metadata:
  name: festin
spec:
  selector: {app: festin}
  ports:
    - port: 80
      targetPort: 8420
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: festin
  annotations:
    nginx.ingress.kubernetes.io/limit-rps: "5"          # brute-force guard for /auth/login
spec:
  rules:
    - host: festin.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: festin
                port: {number: 80}
```

## Scanner as a CronJob

One-shot scans fit Kubernetes CronJobs perfectly:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: festin-recon
spec:
  schedule: "0 6 * * 1"          # Mondays 06:00
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: scan
              image: ghcr.io/cr0hn/festin:latest
              args:
                - scan
                - -f
                - /etc/festin/targets.txt
                - --export
                - sarif
                - --output
                - /output/weekly.sarif
                - --quiet
              volumeMounts:
                - name: targets
                  mountPath: /etc/festin
                - name: output
                  mountPath: /output
          volumes:
            - name: targets
              configMap: {name: festin-targets}
            - name: output
              persistentVolumeClaim: {claimName: festin-reports}
```

## Why `replicas: 1` and `strategy: Recreate`

The dashboard state lives in SQLite on a `ReadWriteOnce` volume. Two pods
can't share it, and a rolling update would overlap old/new pods on the same
claim. [HA](ha.md) covers the supported scaling paths (read-replica UI,
Postgres, external scheduler).