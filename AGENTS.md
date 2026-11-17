# Работа с DIVAi

На Divoom TimeBox Mini используется матрица 11 × 11. Дежурный (`observer`) отвечает за повседневный режим, Режиссёр (`director`) за съёмку. Для старых рисунков предусмотрен импорт `character.json`.

- `studio.py` и `start.command` запускают повседневный и ручной режимы. `faces.py`, `skin.py` и `editor.html` задают рисунки и их формат.
- `shoot.py`, `shoot.html` и `shoot.command` управляют дублем. Реакция привязана к времени воспроизведения видео. Демо находится в `demo/take.mp4`.
- `device.py` и `vendor/timebox_bridge.swift` выводят кадры через Bluetooth на macOS. Происхождение модуля описано в `vendor/UPSTREAM.txt`.

Из корня репозитория запусти `python3 -m unittest discover -s tests`. Для прогона со встроенным роликом используй `bash shoot.command --preview`. Проверка Bluetooth и системного голоса требует Mac с подключённым TimeBox Mini.
