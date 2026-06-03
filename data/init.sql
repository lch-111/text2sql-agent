-- =============================================================================
-- PostgreSQL 初始化脚本（用于 docker-compose 首次启动）
-- 创建与 SQLite 对应的表结构
-- =============================================================================

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    user_id SERIAL PRIMARY KEY,
    username VARCHAR(50) NOT NULL,
    email VARCHAR(100),
    age INTEGER,
    gender VARCHAR(10),
    city VARCHAR(50),
    province VARCHAR(50),
    registration_date DATE,
    vip_level VARCHAR(20),
    is_active BOOLEAN DEFAULT TRUE
);

-- 商品表
CREATE TABLE IF NOT EXISTS products (
    product_id SERIAL PRIMARY KEY,
    product_name VARCHAR(200) NOT NULL,
    category VARCHAR(50),
    price DECIMAL(10, 2),
    cost DECIMAL(10, 2),
    stock INTEGER DEFAULT 0,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 订单表
CREATE TABLE IF NOT EXISTS orders (
    order_id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(user_id),
    product_id INTEGER REFERENCES products(product_id),
    quantity INTEGER DEFAULT 1,
    total_amount DECIMAL(12, 2),
    order_date DATE,
    status VARCHAR(20),
    payment_method VARCHAR(30)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_product_id ON orders(product_id);
CREATE INDEX IF NOT EXISTS idx_orders_order_date ON orders(order_date);
CREATE INDEX IF NOT EXISTS idx_users_province ON users(province);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
