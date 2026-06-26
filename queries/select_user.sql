-- =============================================================
-- MySQL: SELECT user queries
-- =============================================================
-- This file collects common "SELECT user" patterns in MySQL.
-- It covers two distinct scenarios:
--   1. DBA / administrative queries against the mysql.user system table
--      (the MySQL server's account catalog).
--   2. Application queries against a typical `users` table.
-- =============================================================


-- -------------------------------------------------------------
-- 1. Administrative queries (mysql.user system table)
-- -------------------------------------------------------------
-- The mysql.user table stores MySQL accounts. Reading it usually
-- requires the SELECT privilege on the `mysql` database
-- (typically only granted to administrators / root).

-- 1.1 List every MySQL account (user + host pair).
SELECT User, Host
FROM   mysql.user
ORDER  BY User, Host;

-- 1.2 Show the currently authenticated account.
SELECT CURRENT_USER();   -- account used for privilege checks
SELECT USER();           -- account the client connected as

-- 1.3 Show accounts together with their authentication plugin and
--     whether the account is locked or expired (MySQL 5.7+).
SELECT User,
       Host,
       plugin                AS auth_plugin,
       account_locked,
       password_expired
FROM   mysql.user
ORDER  BY User, Host;

-- 1.4 Find accounts that can log in from anywhere ('%').
SELECT User, Host
FROM   mysql.user
WHERE  Host = '%';

-- 1.5 Find accounts with an empty password (security audit).
SELECT User, Host
FROM   mysql.user
WHERE  authentication_string = '' OR authentication_string IS NULL;

-- 1.6 List the privileges granted to a specific account.
SHOW GRANTS FOR 'someuser'@'localhost';


-- -------------------------------------------------------------
-- 2. Application queries (generic `users` table)
-- -------------------------------------------------------------
-- Assumed schema (adjust column names to match your project):
--
--   CREATE TABLE users (
--     id           INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
--     username     VARCHAR(64)  NOT NULL UNIQUE,
--     email        VARCHAR(255) NOT NULL UNIQUE,
--     password_hash CHAR(60)    NOT NULL,
--     is_active    TINYINT(1)   NOT NULL DEFAULT 1,
--     created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
--     updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
--                                ON UPDATE CURRENT_TIMESTAMP
--   );

-- 2.1 Select all users (small tables only -- avoid SELECT * in production).
SELECT id, username, email, is_active, created_at
FROM   users
ORDER  BY id;

-- 2.2 Select a single user by primary key.
SELECT id, username, email, is_active, created_at
FROM   users
WHERE  id = ?
LIMIT  1;

-- 2.3 Select a user by username (e.g. for login lookup).
SELECT id, username, email, password_hash, is_active
FROM   users
WHERE  username = ?
LIMIT  1;

-- 2.4 Select a user by email (case-insensitive on most collations).
SELECT id, username, email
FROM   users
WHERE  email = ?
LIMIT  1;

-- 2.5 List only active users, paginated.
SELECT id, username, email, created_at
FROM   users
WHERE  is_active = 1
ORDER  BY created_at DESC
LIMIT  20 OFFSET 0;

-- 2.6 Search users by partial username or email.
SELECT id, username, email
FROM   users
WHERE  username LIKE CONCAT('%', ?, '%')
   OR  email    LIKE CONCAT('%', ?, '%')
ORDER  BY username
LIMIT  50;

-- 2.7 Count users (total and active).
SELECT COUNT(*)                                AS total_users,
       SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) AS active_users
FROM   users;

-- 2.8 Most recently registered users.
SELECT id, username, email, created_at
FROM   users
ORDER  BY created_at DESC
LIMIT  10;
