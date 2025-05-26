# Копируем весь код до функции main()...

async def main():
    """Запуск бота"""
    # Создание приложения
    application = Application.builder().token(TOKEN).build()

    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("chatid", get_chat_id))
    application.add_handler(CallbackQueryHandler(handle_subscription_check, pattern="^check_subscription$"))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    await application.initialize()
    await application.start()

    # Настройка периодической отправки аналитики
    job_queue = application.job_queue
    job_queue.run_repeating(send_analytics, interval=300, first=10)

    # Отправка сообщения о запуске бота
    async def send_startup_message():
        if ANALYTICS_CHAT_ID:
            try:
                await application.bot.send_message(
                    chat_id=ANALYTICS_CHAT_ID,
                    text="🚀 *Бот успешно запущен!*\n\n📅 Время запуска: " + datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
                    parse_mode=ParseMode.MARKDOWN
                )
                logger.info("Отправлено сообщение о запуске бота")
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения о запуске: {e}")
    
    # Запускаем отправку сообщения о старте
    await send_startup_message()

    try:
        if os.getenv('RENDER'):
            # Запуск в режиме webhook на Render.com
            port = int(os.getenv('PORT', 3000))
            webhook_url = f"{os.getenv('RENDER_EXTERNAL_URL')}/{TOKEN}"
            
            # Принудительно устанавливаем webhook
            await application.bot.set_webhook(url=webhook_url)
            logger.info(f"Webhook установлен на URL: {webhook_url}")
            
            # Запускаем webhook сервер
            await application.updater.start_webhook(
                listen='0.0.0.0',
                port=port,
                url_path=TOKEN,
                webhook_url=webhook_url,
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=True
            )
            await application.updater.start_webhook_server()
            await application.idle()
        else:
            # Локальный запуск в режиме polling
            await application.updater.start_polling(drop_pending_updates=True)
            await application.idle()
    finally:
        if application.updater and application.updater.running:
            await application.updater.stop()
        await application.stop()
        await application.shutdown()

if __name__ == '__main__':
    try:
        import asyncio
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем!")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        logger.info("Бот завершил работу")
