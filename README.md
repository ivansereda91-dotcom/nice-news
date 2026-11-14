# Новости Ниццы

Телеграм-бот для семьи: каждое утро сводка новостей Ниццы и окрестностей (погода, тревоги, город и транспорт,
культура, спорт), по четвергам — афиша на выходные. Новости ищет Claude через веб-поиск, погода — Open-Meteo.

Установка на сервер:

```
git clone https://github.com/ivansereda91-dotcom/nice-news /opt/nice-news
bash /opt/nice-news/setup.sh
```

Настройки — в `/opt/nice-news/.env` (время, день афиши, модель). После правки: `systemctl restart nice-news`.
