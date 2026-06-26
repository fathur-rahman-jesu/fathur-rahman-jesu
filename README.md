# fathur-rahman-jesu

## MySQL: SELECT user queries

See [`queries/select_user.sql`](queries/select_user.sql) for a collection of
common MySQL `SELECT` queries related to users. It covers two scenarios:

1. **Administrative queries** against the `mysql.user` system table
   (list accounts, show the current user, audit empty passwords, etc.).
2. **Application queries** against a typical `users` table (lookup by id,
   username, or email, pagination, search, counts, recent signups).

Quick example — list every MySQL account on the server:

```sql
SELECT User, Host
FROM   mysql.user
ORDER  BY User, Host;
```

Quick example — look up an application user by username:

```sql
SELECT id, username, email, password_hash, is_active
FROM   users
WHERE  username = ?
LIMIT  1;
```
