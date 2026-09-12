import uuid
import threading

_thread_locals = threading.local()

def get_current_request_id():
    return getattr(_thread_locals, "request_id", None)

class RequestIDMiddleware:
    """
    Reads X-Request-ID from incoming headers, or generates one.
    Stores it in thread-local storage for audit logging, and adds it
    to the response headers.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get("X-Request-ID")
        
        if request_id:
            # Bound length just to be safe
            request_id = str(request_id)[:128]
        else:
            request_id = str(uuid.uuid4())
            
        request.id = request_id
        _thread_locals.request_id = request_id

        try:
            response = self.get_response(request)
        finally:
            # cleanup
            if hasattr(_thread_locals, "request_id"):
                del _thread_locals.request_id

        response["X-Request-ID"] = request_id
        return response
