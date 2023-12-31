from aiohttp import web
import ssl
import subprocess
import argparse
import os
import logging
import asyncio

logging.basicConfig(level=logging.DEBUG, format=" - %(message)s")
ERRONOUS_PASSWORD_TRIES = 0


async def check_authenticated(request):
    logging.info("check_authenticated")
    global ERRONOUS_PASSWORD_TRIES
    token = request.headers.get("Authorization")
    if not request.app.get("auth_password", None):
        return True
    if token == request.app.get("auth_password"):
        return True
    ERRONOUS_PASSWORD_TRIES += 1
    logging.info(f'sensitivity: {request.app.get("sensitive")}')
    if int(
        request.app.get("sensitive")
    ) != -1 and ERRONOUS_PASSWORD_TRIES >= int(request.app.get("sensitive")):
        logging.critical("Too many erronous tries")
        os._exit(-1)
    return False


async def login_required_middleware(request, handler):
    logging.info("login required middleware")
    authenticated = await check_authenticated(request)
    if not authenticated:
        return web.Response(text="Unauthorized", status=401)
    return await handler(request)


def login_required(handler):
    logging.info("login required")

    async def wrapped_handler(request):
        return await login_required_middleware(request, handler)

    return wrapped_handler


@login_required
async def handle(request):
    logging.info("HANDLE_SCRIPT")
    script_name = request.match_info.get("script_name")

    if script_name:
        scripts_folder = os.path.join(
            os.getcwd(), request.app.get("scripts_folder", "")
        )
        script_path = os.path.join(scripts_folder, script_name)

        if os.path.exists(script_path):
            try:
                process = await asyncio.create_subprocess_exec(
                    "bash",
                    script_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                response = web.StreamResponse()
                await response.prepare(request)

                # Set Content-Type header for streaming response
                response.content_type = "application/octet-stream"

                while True:
                    stdout_data = await process.stdout.read(
                        1024
                    )  # Read in chunks
                    if not stdout_data:
                        break

                    await response.write(stdout_data)

                await response.write_eof()

                # Wait for the process to finish and get the return code
                return_code = await process.wait()

                # If the process exits with a non-zero code, handle the error
                if return_code != 0:
                    error_message = f"Script execution failed with return code {return_code}"
                    await response.write(error_message.encode("utf-8"))

            except asyncio.CancelledError:
                logging.error("Request cancelled by the client")
                raise
            except Exception as e:
                logging.error(f"Error occurred: {str(e)}")
                return web.Response(text=f"Error: {str(e)}", status=500)
            return response

        else:
            return web.Response(text="Error: Script not found", status=404)

    else:
        return web.Response(text="Error: No script name provided", status=400)


async def handle_status(request):
    logging.info("HANDLE_STATUS")
    return web.json_response({"status": "ok"})


async def handle_list_commands(request):
    commands = []
    for filename in os.listdir(request.app.get("scripts_folder")):
        if filename.endswith(".sh") or filename.endswith(".py"):
            commands.append(filename)
    return web.json_response({"commands": commands})


def create_ssl_context(certfile, keyfile):
    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    try:
        ssl_context.load_cert_chain(certfile=certfile, keyfile=keyfile)
        return ssl_context
    except:
        logging.exception("Could not instanciate ssl context")
        return None


def run():
    parser = argparse.ArgumentParser(
        description="Run a server for executing custom scripts over HTTPS."
    )
    parser.add_argument(
        "--host",
        default=os.getenv("HOST", "0.0.0.0"),
        help="Host IP address to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "8443")),
        help="Port number for the server (default: 8443)",
    )
    parser.add_argument(
        "--certfile",
        default=os.getenv("CERT_FILE", "path/to/your/certfile.pem"),
        help="Path to SSL certificate file",
    )
    parser.add_argument(
        "--keyfile",
        default=os.getenv("KEY_FILE", "path/to/your/keyfile.pem"),
        help="Path to SSL private key file",
    )
    parser.add_argument(
        "--scripts-folder",
        default=os.getenv("SCRIPTS_FOLDER", os.getcwd()),
        help="Path to the folder containing available scripts",
    )
    parser.add_argument(
        "--auth-password",
        default=os.getenv("AUTH_PASSWORD", None),
        help="Password for authentication (default: None)",
    )
    parser.add_argument(
        "--sensitive",
        default=1,
        help="Amount of wrong password the server can receive before killing it-self",
    )
    parser.add_argument(
        "--ntfy", default=None, help="ntfy.sh notification room"
    )

    args = parser.parse_args()
    HOST = args.host
    PORT = args.port
    CERT_FILE = args.certfile
    KEY_FILE = args.keyfile
    SCRIPTS_FOLDER = args.scripts_folder
    AUTH_PASSWORD = args.auth_password
    NOTIFY = args.ntfy
    SENSITIVE = args.sensitive

    if AUTH_PASSWORD is None:
        logging.info(
            "Warning: No authentication password provided. Requests will not be authenticated."
        )
    app = web.Application()
    app["auth_password"] = AUTH_PASSWORD
    app["notify"] = NOTIFY
    app["scripts_folder"] = SCRIPTS_FOLDER
    app["sensitive"] = SENSITIVE
    app.router.add_get("/status", handle_status)
    app.router.add_get("/", login_required(handle_list_commands))
    app.router.add_post("/{script_name}", login_required(handle))

    ssl_context = create_ssl_context(CERT_FILE, KEY_FILE)
    logging.info(f"ssl_context is : {ssl_context}")
    web.run_app(
        app, host=HOST, port=PORT, ssl_context=ssl_context, access_log=None
    )


if __name__ == "__main__":
    run()
