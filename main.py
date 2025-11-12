import asyncio
import logging
from telegram import Bot
from telegram.ext import Application, CommandHandler
import aiohttp
import json
import time
from datetime import datetime
from config import TELEGRAM_BOT_TOKEN, YANDEX_LAVKA_URL, TARGET_ADDRESS, CHECK_INTERVAL

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class YandexLavkaMonitor:
    def __init__(self, bot_token):
        self.bot_token = bot_token
        self.application = Application.builder().token(bot_token).build()
        self.subscribers = set()
        self.last_slots = []
        self.setup_handlers()
        
    def setup_handlers(self):
        """Настройка обработчиков команд"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("subscribe", self.subscribe))
        self.application.add_handler(CommandHandler("unsubscribe", self.unsubscribe))
        self.application.add_handler(CommandHandler("status", self.status))
        self.application.add_handler(CommandHandler("info", self.info))
        
    async def start(self, update, context):
        """Обработчик команды /start"""
        welcome_text = """
🤖 <b>Бот для отслеживания слотов в Яндекс Лавке</b>

📍 <b>Адрес:</b> Среднерогатская 20, Санкт-Петербург

📋 <b>Команды:</b>
/subscribe - Подписаться на уведомления
/unsubscribe - Отписаться от уведомлений  
/status - Проверить текущее наличие слотов
/info - Информация о боте

🔔 Бот автоматически проверяет слоты каждую минуту и присылает уведомления при их появлении!
        """
        await update.message.reply_text(welcome_text, parse_mode='HTML')
        
    async def info(self, update, context):
        """Информация о боте"""
        info_text = f"""
📊 <b>Статистика бота:</b>

👥 Подписчиков: {len(self.subscribers)}
🕐 Последняя проверка: {datetime.now().strftime('%H:%M:%S')}
📍 Отслеживаемый адрес: {TARGET_ADDRESS}
⏰ Интервал проверки: {CHECK_INTERVAL} секунд
        """
        await update.message.reply_text(info_text, parse_mode='HTML')
        
    async def subscribe(self, update, context):
        """Подписка на уведомления"""
        user_id = update.effective_user.id
        self.subscribers.add(user_id)
        await update.message.reply_text("✅ Вы подписаны на уведомления о слотах! Бот будет присылать уведомления при появлении доступных слотов доставки.")
        
    async def unsubscribe(self, update, context):
        """Отписка от уведомлений"""
        user_id = update.effective_user.id
        if user_id in self.subscribers:
            self.subscribers.remove(user_id)
            await update.message.reply_text("❌ Вы отписаны от уведомлений.")
        else:
            await update.message.reply_text("ℹ️ Вы не были подписаны.")
            
    async def status(self, update, context):
        """Проверка текущего статуса слотов"""
        try:
            await update.message.reply_text("🔍 Проверяю доступные слоты...")
            slots = await self.check_slots()
            if slots:
                message = self.format_slots_message(slots)
            else:
                message = "❌ На данный момент нет доступных слотов для доставки."
            await update.message.reply_text(message, parse_mode='HTML')
        except Exception as e:
            await update.message.reply_text(f"⚠️ Ошибка при проверке: {str(e)}")
    
    async def check_slots(self):
        """Проверка доступных слотов"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/json',
                'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            }
            
            async with aiohttp.ClientSession() as session:
                # Попробуем найти магазин через поиск
                search_url = f"https://lavka.yandex.ru/api/v2/search"
                params = {
                    'text': TARGET_ADDRESS,
                    'zone': 'spb'
                }
                
                async with session.get(search_url, headers=headers, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"Search API response: {json.dumps(data, ensure_ascii=False)[:500]}")
                        
                        # Пытаемся найти магазин в ответе
                        stores = data.get('found', {}).get('stores', [])
                        if not stores:
                            # Альтернативный путь поиска
                            stores = data.get('stores', [])
                        
                        for store in stores:
                            address = store.get('address', {}).get('full', '').lower()
                            name = store.get('name', '').lower()
                            logger.info(f"Checking store: {name} - {address}")
                            
                            if 'среднерогатская' in address and '20' in address:
                                store_id = store['id']
                                logger.info(f"Found target store: {store['name']}")
                                
                                # Получаем слоты для этого магазина
                                slots = await self.get_store_slots(session, store_id)
                                return slots
                    
                    # Если не нашли через поиск, попробуем прямой метод
                    return await self.direct_slots_check(session)
                        
        except Exception as e:
            logger.error(f"Error checking slots: {str(e)}")
            return None
    
    async def get_store_slots(self, session, store_id):
        """Получение слотов для конкретного магазина"""
        try:
            slots_url = f"https://lavka.yandex.ru/api/v1/stores/{store_id}/slots"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json',
            }
            
            async with session.get(slots_url, headers=headers) as response:
                if response.status == 200:
                    slots_data = await response.json()
                    return self.parse_slots(slots_data)
                else:
                    logger.error(f"Slots API error: {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Error getting store slots: {str(e)}")
            return None
    
    async def direct_slots_check(self, session):
        """Прямая проверка слотов через основной API"""
        try:
            # Альтернативный endpoint для получения слотов
            slots_url = "https://lavka.yandex.ru/api/v4/slots"
            params = {
                'latitude': 59.909,  # Примерные координаты СПб
                'longitude': 30.315,
                'zone': 'spb'
            }
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json',
            }
            
            async with session.get(slots_url, headers=headers, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"Direct slots check response received")
                    return self.parse_slots(data)
                else:
                    logger.error(f"Direct slots API error: {response.status}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error in direct slots check: {str(e)}")
            return None
    
    def parse_slots(self, slots_data):
        """Парсинг данных о слотах"""
        available_slots = []
        
        # Разные возможные структуры ответа
        slots_list = slots_data.get('slots', [])
        if not slots_list:
            slots_list = slots_data.get('available_slots', [])
        
        for day_slots in slots_list:
            date = day_slots.get('date', '')
            slots = day_slots.get('slots', [])
            
            for slot in slots:
                if slot.get('available', False) and slot.get('type') == 'regular':
                    available_slots.append({
                        'date': date,
                        'start_time': slot.get('from', ''),
                        'end_time': slot.get('to', ''),
                        'price': slot.get('price', {}).get('value'),
                        'currency': slot.get('price', {}).get('currency', '₽')
                    })
        
        logger.info(f"Parsed {len(available_slots)} available slots")
        return available_slots
    
    def format_slots_message(self, slots):
        """Форматирование сообщения о слотах"""
        if not slots:
            return "❌ Нет доступных слотов"
        
        message = "🎉 <b>Доступные слоты для доставки:</b>\n\n"
        for i, slot in enumerate(slots[:15], 1):  # Ограничиваем 15 слотами
            try:
                date_obj = datetime.strptime(slot['date'], '%Y-%m-%d')
                date_str = date_obj.strftime('%d.%m.%Y')
                
                message += f"<b>{i}. 📅 {date_str}</b>\n"
                message += f"   🕒 {slot['start_time']} - {slot['end_time']}\n"
                if slot['price']:
                    message += f"   💰 {slot['price']} {slot['currency']}\n"
                message += "\n"
            except Exception as e:
                logger.error(f"Error formatting slot {slot}: {e}")
                continue
        
        if len(slots) > 15:
            message += f"<i>... и ещё {len(slots) - 15} слотов</i>\n"
        
        message += f"\n🕐 <i>Проверено: {datetime.now().strftime('%H:%M:%S')}</i>"
        return message
    
    def has_new_slots(self, current_slots):
        """Проверка на наличие новых слотов"""
        if not current_slots:
            return False
            
        current_slots_str = str([(s.get('date', ''), s.get('start_time', '')) for s in current_slots])
        last_slots_str = str([(s.get('date', ''), s.get('start_time', '')) for s in self.last_slots])
        
        has_changes = current_slots_str != last_slots_str
        
        if has_changes:
            self.last_slots = current_slots.copy()
            logger.info(f"Slots changed: {len(current_slots)} available")
            
        return has_changes
    
    async def send_notifications(self, slots):
        """Отправка уведомлений подписчикам"""
        if not self.subscribers:
            logger.info("No subscribers to notify")
            return
            
        message = self.format_slots_message(slots)
        successful_sends = 0
        
        for user_id in list(self.subscribers):
            try:
                await self.application.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode='HTML'
                )
                successful_sends += 1
                logger.info(f"Notification sent to user {user_id}")
                
                # Небольшая задержка между отправками
                await asyncio.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Failed to send notification to {user_id}: {str(e)}")
                # Удаляем пользователя, если бот заблокирован
                if "bot was blocked" in str(e).lower() or "chat not found" in str(e).lower():
                    self.subscribers.remove(user_id)
                    logger.info(f"Removed blocked user {user_id}")
        
        logger.info(f"Notifications sent: {successful_sends}/{len(self.subscribers)}")
    
    async def monitoring_loop(self):
        """Основной цикл мониторинга"""
        logger.info("🚀 Starting Yandex Lavka monitoring bot...")
        logger.info(f"📍 Target address: {TARGET_ADDRESS}")
        logger.info(f"⏰ Check interval: {CHECK_INTERVAL} seconds")
        
        # Первоначальная проверка
        initial_slots = await self.check_slots()
        if initial_slots:
            logger.info(f"Initial check: {len(initial_slots)} slots available")
            self.last_slots = initial_slots.copy()
        else:
            logger.info("Initial check: no slots available")
        
        while True:
            try:
                logger.info("🔍 Checking for available slots...")
                current_slots = await self.check_slots()
                
                if current_slots:
                    logger.info(f"Found {len(current_slots)} available slots")
                    
                    # Проверяем, есть ли новые слоты
                    if self.has_new_slots(current_slots):
                        logger.info("🆕 New slots detected, sending notifications")
                        await self.send_notifications(current_slots)
                    else:
                        logger.info("✅ Slots unchanged, no notifications sent")
                else:
                    logger.info("❌ No available slots found")
                
                # Ждем перед следующей проверкой
                await asyncio.sleep(CHECK_INTERVAL)
                
            except Exception as e:
                logger.error(f"❌ Error in monitoring loop: {str(e)}")
                await asyncio.sleep(CHECK_INTERVAL)

async def main():
    """Основная функция"""
    try:
        monitor = YandexLavkaMonitor(TELEGRAM_BOT_TOKEN)
        
        # Запускаем мониторинг в фоне
        asyncio.create_task(monitor.monitoring_loop())
        
        logger.info("🤖 Bot is starting...")
        
        # Запускаем бота
        await monitor.application.run_polling()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")

if __name__ == '__main__':
    asyncio.run(main())
