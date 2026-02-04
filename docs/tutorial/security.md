We've covered a lot so far, but we still don't know *who* is sending these requests.

To solve this, let's use a simple login system with <abbr title="JSON Web Tokens">JWTs</abbr>.

First, let's make sure the JWT library is installed on the backend.

<div class="termy">

```console
$ # Make sure you are in the venv.
$ echo "pyjwt" >> backend/requirements.txt
$ pip install -r backend/requirements.txt
```

</div>

Now, we can edit the backend file.

```python title="backend/src/app.py"
from ephaptic import Ephaptic, identity_loader, active_user
from datetime import datetime, timezone, timedelta
import jwt
import random, string

JWT_SECRET = ''.join(random.choices(string.ascii_letters+string.digits, k=32)) # We would use an actual JWT secret in a `.env` in production!

def generate_token(user_id):
    payload = {
        'sub': user_id,
        'exp': datetime.now(timezone.utc) + timedelta(weeks=52)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')

def verify_token(token):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return payload['sub']
    except:
        return None

@identity_loader # This decorator tells ephaptic the function we are using to load identity, from the auth payload.
def load_identity(auth):
    token = auth.get("_jwt")
    if token:
        return verify_token(token)
    return None

@ephaptic.expose
async def login(username: str) -> str: # Expose a login function that returns a JWT.
    # In a real app, we would check a database and hash passwords, but for demonstration purposes, we'll only require the username to log in.
    if username == "admin":
        # Assume the `admin` user has an ID of `1`.
        return generate_token(1)
    
    raise Exception("Invalid username")

@ephaptic.expose
async def get_user() -> str:
    return active_user()
```

Now, on the frontend, we can do this:

```typescript
const client = connect({ auth: { _jwt: window.localStorage.getItem('token') } }) as unknown as EphapticService;
```

But we haven't got a way to *set* the token in localStorage yet.

So, let's add login handling to the frontend.

=== "React"

    ```typescript title="frontend/src/App.tsx"
    const token = localStorage.getItem('token');

    const client = connect({
        url: "ws://localhost:8000/_ephaptic",
        auth: { _jwt: token }
    }) as unknown as EphapticService;

    function App() {
        const [user, setUser] = useState<string | null>(null);

        useEffect(() => {
            if (token) {
                client.get_user()
                    .then(setUser)
                    .catch(() => {
                        console.warn("Invalid token.")
                        localStorage.removeItem('token');
                    });
            }
        }, []);

        const handleLogin = async () => {
            const newToken = await client.login("admin"); // In a real app, we'd have a login form with username and password inputs.

            localStorage.setItem('token', newToken);
            window.location.reload(); // Reloading the page re-connects the client with the new auth token.
        };

        const handleLogout = () => {
            localStorage.removeItem('token');
            window.location.reload();
        }

        if (!token) return <button onClick={handleLogin}>Login</button>;
        else return (
            <div>
                <h1>Welcome, {user}</h1>
                <button onClick={handleLogout}>Logout</button>
            </div>
        );
    }
    ```

=== "Svelte"

    ```html title="src/routes/+page.svelte"
    <script lang="ts">
        // Making sure the code only runs in the browser, and not SSR.
        const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;

        const client = connect({
            url: "ws://localhost:8000/_ephaptic",
            auth: { _jwt: token }
        }) as unknown as EphapticService;

        let user = $state<string | null>(null);

        onMount(async () => {
            if (token) {
                try {
                    user = await client.get_user();
                } catch {
                    console.warn("Invalid token.")
                    localStorage.removeItem('token');
                }
            }
        });

        async function handleLogin() {
            const newToken = await client.login('admin'); // In a real app, we'd have a login form with username and password inputs.

            localStorage.setItem('token', newToken);
            window.location.reload(); // Reloading the page re-connects the client with the new auth token.
        }

        function handleLogout() {
            localStorage.removeItem('token');
            window.location.reload();
        }
    </script>

    {#if !token}
        <button onclick={handleLogin}>Login</button>
    {:else}
        <h1>Welcome, {user}</h1>
        <button onclick={handleLogout}>Logout</button>
    {/if}
    ```