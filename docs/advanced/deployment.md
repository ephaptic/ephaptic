If you're going to production, you obviously can't connect to `localhost:8000` on your site.

You'll need to connect to the backend part of your site, from the frontend.

But how? Let's show you.

## Docker

For this tutorial, we'll be using Docker containers named `frontend`, `backend`, and `gateway`.

First, set up your file structure.

```
├── backend
│   └── ...
├── frontend
│   └── ...
├── gateway
│    └── nginx.conf
├── docker-compose.yml
├── .gitignore
└── .dockerignore
```

Now, let's create the `docker-compose.yml` file.

```yaml title="docker-compose.yml"
services:
  backend:
    build: ./backend
    expose:
      - "8000"
    networks:
      - internal
    restart: unless-stopped

  frontend:
    build: ./frontend
    depends_on:
      - backend
    expose:
      - "3000"
    environment:
      - NODE_ENV=production
    networks:
      - internal
    restart: unless-stopped

  gateway:
    image: nginx:alpine
    depends_on:
      - frontend
      - backend
    volumes:
      # Mount the `gateway/nginx.conf` into the container.
      - ./gateway/nginx.conf:/etc/nginx/conf.d/default.conf
    networks:
      - internal
    ports:
      - "80:80"
      - "443:443"
    restart: unless-stopped

networks:
  internal:
    driver: bridge
```

Next, we need to create `gateway/nginx.conf`.

```nginx title="nginx.conf"
server {
    listen 80;

    location / {
        proxy_pass http://frontend:3000;

        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_read_timeout 300s;
    }

    location /_ephaptic {
        proxy_pass http://backend:8000/_ephaptic;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_buffering off;

        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_connect_timeout 60s;
    }

    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss;
}
```

Now, run `docker compose up --build` and head to [localhost:80](http://localhost:80) in your browser.

!!! tip
    Make sure we remove the `url` parameter in the ephaptic constructor, as now it automatically defaults to `/_ephaptic` on the same host.

Congrats! We've got a production-ready ephaptic working!

Well, not fully yet. We still need Redis for horizontal scaling (multiple workers), if you are using the [identity loading](../tutorial/security.md) feature.

## Redis

Let's modify `backend/src/app.py`:

```python title="backend/src/app.py"
import os

ephaptic = Ephaptic.from_app(app, redis_url=os.getenv("REDIS_URL_BASE")+"/0")
```

Now, we need to pass the base redis url to the backend container, and also create a `redis` container in Docker.

```yaml title="docker-compose.yml"
services:
  backend:
    build: ./backend
    depends_on:
      redis
    environment:
      REDIS_URL_BASE: "redis://redis:6379"
    expose:
      - "8000"
    networks:
      - internal
    restart: unless-stopped

  frontend:
    ...
  
  gateway:
    ...

  redis:
    image: redis:alpine
    expose:
      - "6379"
    volumes:
      - redis-data:/data
    networks:
      - internal
    restart: unless-stopped
    
networks:
  ...

volumes:
  redis-data:
```

Now, rerun the app.

```console
$ docker compose up --build
```

You may not notice anything, but now, each `uvicorn` worker knows which clients that they have connected, and they're able to talk to each other.

This means, even in a distributed system with hundreds of nodes running the backend container, if they're all hooked up to one Redis instance, an event emitted by one node (`await ephaptic.to(user).emit(event)`) will always reach the node that the target user is connected to, which will then broadcast it to the frontend.