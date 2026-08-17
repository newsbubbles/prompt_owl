class APIError(ValueError):
    def __init__(self, status, message, data=None):
        super().__init__(f"API Error ({status}): {message}")
        self.status = status
        self.message = message
        self.data = data

    def fatal(self):
        # credentials and credit: retrying cannot help
        return self.status in (401, 402, 403)

    def to_dict(self):
        return {'status': self.status, 'message': self.message, 'data': self.data}


class ValidationError(ValueError):
    def __init__(self, code, message, data=None):
        super().__init__(f"Error ({code}): {message}\n\t{data}")
        self.code = code
        self.message = message
        self.data = data

    def to_dict(self):
        return {'code': self.code, 'message': self.message, 'data': self.data}