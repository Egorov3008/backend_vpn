from config import ADMIN_ID


class CheckedUser:
    """Класс для проверки пользователя в списке администраторов"""

    def check(self, user_id: int):
        return user_id in ADMIN_ID
