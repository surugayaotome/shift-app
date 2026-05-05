import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import urllib.parse

st.set_page_config(page_title="デバッグモード", layout="wide")

# --- データベース接続関数（エラー表示強化） ---
@st.cache_resource
def get_engine():
    try:
        # 1. Secretsからデータを取得できるかチェック
        if "database" not in st.secrets:
            st.error("Secretsに [database] セクションが見つかりません。")
            return None
        
        raw_uri = st.secrets["database"]["uri"]
        
        # 2. パスワードの特殊文字対策（@ や : をエンコード）
        if "@" in raw_uri:
            # プロトコル部分を分離
            prefix, rest = raw_uri.split("://")
            # ユーザー名:パスワード 部分と ホスト以降を分離
            user_pass, host_info = rest.rsplit("@", 1)
            if ":" in user_pass:
                user, password = user_pass.split(":", 1)
                safe_password = urllib.parse.quote_plus(password)
                # 再構築
                raw_uri = f"{prefix}://{user}:{safe_password}@{host_info}"

        # 3. SQLAlchemy形式に補正
        if raw_uri.startswith("postgresql://"):
            raw_uri = raw_uri.replace("postgresql://", "postgresql+psycopg2://", 1)
        
        # 4. SSL設定を強制
        if "?" not in raw_uri:
            raw_uri += "?sslmode=require"
        
        return create_engine(raw_uri)
        
    except Exception as e:
        st.error(f"⚠️ 接続エンジンの作成に失敗しました: {e}")
        return None

# --- メイン処理 ---
st.title("🚀 シフト管理システム：接続テスト中")

engine = get_engine()

if engine:
    try:
        # 実際にDBを叩いてみる
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            st.success("✅ データベース接続に成功しました！")
            
            # テーブル作成を試みる
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS shift_data (
                    day TEXT,
                    staff_name TEXT,
                    off_status TEXT,
                    shift_json TEXT,
                    PRIMARY KEY (day, staff_name)
                );
            """))
            conn.commit()
            st.info("テーブルの準備もOKです。")
            
    except Exception as e:
        # 🚨 ここで画面に生のエラーを表示させる
        st.error("⚠️ データベースとの通信に失敗しました。")
        st.exception(e) # これで詳細なスタックトレースが画面に出ます
else:
    st.warning("エンジンが作成されていないため、接続テストをスキップしました。")

st.write("---")
st.write("もし赤いボックスの中に `password authentication failed` と出ていたら、パスワードが間違っています。")
