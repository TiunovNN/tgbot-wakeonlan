#!/usr/bin/env python
import logging
from logging.handlers import TimedRotatingFileHandler

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.files import JSONStorage
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ContentType

import wake_on_lan
import settings


log_format = (
    '[%(asctime)s] %(levelname)-8s %(name)-12s %(message)s')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    handlers=[TimedRotatingFileHandler(settings.LOG_FILE, when='d')],
)

# Initialize bot and dispatcher
bot = Bot(token=settings.API_TOKEN)
dp = Dispatcher(bot, storage=JSONStorage('db.json'))
dp.middleware.setup(LoggingMiddleware())


class UserState(StatesGroup):
    NEW_USER = State()
    REGISTERED = State()
    FRAUD = State()


def make_menu():
    keyboard = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
    )
    keyboard.add(types.KeyboardButton('Включить компьютер'))
    keyboard.add(types.KeyboardButton('Сброс регистрации'))
    return keyboard


def start_menu():
    keyboard = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
    )
    keyboard.add(types.KeyboardButton('/start'))
    return keyboard


@dp.message_handler(commands=['start'])
async def start_new_user(message: types.Message):
    """
    This handler will be called when user sends `/start` or `/help` command
    """
    state = dp.current_state(user=message.from_user.id)
    state_status = await state.get_state(UserState.NEW_USER)
    if state_status is None:
        button = types.KeyboardButton(
            'Отправить свой номер',
            request_contact=True,
            callback_data='contact'
        )
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        keyboard.add(button)
        await message.answer('Мы еще не знакомы. Пожалуйста, представься', reply_markup=keyboard)
        return

    data = await state.get_data()
    logging.error(f'State status: {state_status}')
    logging.error(f'Data: {data}')
    name = data['name']
    await message.reply(f'Привет, {name}!', reply_markup=make_menu())


@dp.message_handler(content_types=[ContentType.CONTACT])
async def register(message: types.Message):
    state = dp.current_state(user=message.from_user.id)
    await state.reset_state()
    if message.contact.user_id != message.from_user.id:
        await message.answer('Не пытайся меня обмануть')
        return

    phone_number = message.contact.phone_number
    if not phone_number.startswith('+'):
        phone_number = f'+{phone_number}'

    user_data = settings.DB.get(phone_number)
    if not user_data:
        data = await state.get_data()
        data['fraud'] = data.setdefault('fraud', 0) + 1
        await state.update_data(data)
        await message.answer(
            'Я тебя не знаю. Если ты считаешь это ошибкой, '
            'обратись к системному администратору.'
        )
        return

    await state.update_data(user_data)
    await state.set_state(UserState.REGISTERED)
    await message.answer(f'Привет, {user_data["name"]}!', reply_markup=make_menu())


@dp.message_handler(regexp='Сброс регистрации', state=UserState.REGISTERED)
async def reset(message: types.Message, state: FSMContext):
    await state.reset_state()
    await message.answer('Состояние сброшено', reply_markup=start_menu())


@dp.message_handler(regexp='Включить компьютер', state=UserState.REGISTERED)
async def wakeup(message: types.Message, state: FSMContext):
    data = await state.get_data()
    computer = data['computer']
    await wake_on_lan.send_packet(computer)
    await message.answer(f'Компьютер {computer} запускается..', reply_markup=make_menu())


@dp.message_handler(state='*', content_types=ContentType.TEXT)
async def unknown_command(message: types.Message, state: FSMContext):
    user_state = await state.get_state()
    logging.error(f'unknown user state: {user_state}')
    if user_state == UserState.REGISTERED.state:
        await message.answer('Доступны только команды из меню', reply_markup=make_menu())
        return

    await message.answer('Мы не знакомы', reply_markup=start_menu())


async def shutdown(dispatcher: Dispatcher):
    await dispatcher.storage.close()
    await dispatcher.storage.wait_closed()


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_shutdown=shutdown)
