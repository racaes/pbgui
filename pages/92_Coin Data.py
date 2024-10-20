import streamlit as st
import pbgui_help
from pbgui_func import set_page_config, is_session_state_initialized, info_popup
from PBCoinData import CoinData
from Exchange import Exchanges

def view_coindata():
    # Init coins
    if not "coindata" in st.session_state:
        st.session_state.coindata = CoinData()
    coindata  = st.session_state.coindata
    # Navigation
    with st.sidebar:
        if st.button(":material/settings:"):
            st.session_state.setup_coindata = True
            st.rerun()
    # Init session states for keys
    if "view_coindata_exchange" in st.session_state:
        if st.session_state.view_coindata_exchange != coindata.exchange:
            coindata.exchange = st.session_state.view_coindata_exchange
    if "view_coindata_market_cap" in st.session_state:
        if st.session_state.view_coindata_market_cap != coindata.market_cap:
            coindata.market_cap = st.session_state.view_coindata_market_cap
    if "view_coindata_vol_mcap" in st.session_state:
        if st.session_state.view_coindata_vol_mcap != coindata.vol_mcap:
            coindata.vol_mcap = st.session_state.view_coindata_vol_mcap
    # Display
    col_1, col_2, col_3, col_4, col_5 = st.columns([1,1,1,1,1])
    with col_1:
        st.selectbox('Exchange',coindata.exchanges, index=coindata.exchange_index, key="view_coindata_exchange")
    with col_2:
        st.number_input("market_cap", min_value=0, value=coindata.market_cap, step=50, format="%.d", key="view_coindata_market_cap", help=pbgui_help.market_cap)
    with col_3:
        st.number_input("vol/mcap", min_value=0.0, value=coindata.vol_mcap, step=0.05, format="%.2f", key="view_coindata_vol_mcap", help=pbgui_help.vol_mcap)
    column_config = {
        "price": st.column_config.NumberColumn(format="%.8f"),
        "link": st.column_config.LinkColumn(display_text="CoinMarketCap")
    }
    if coindata.symbols_data:
        st.dataframe(coindata.symbols_data, height=36+(len(coindata.symbols_data))*35, use_container_width=True, column_config=column_config)

def setup_coindata():
    # Init market
    if not "coindata" in st.session_state:
        st.session_state.coindata = CoinData()
    coindata  = st.session_state.coindata
    # Navigation
    with st.sidebar:
        if st.button(":material/home:"):
            del st.session_state.setup_coindata
            st.rerun()
        if st.button(":material/save:"):
            coindata.save_config()
            info_popup("Config saved")
    # Init session states for keys
    if "edit_coindata_api_key" in st.session_state:
        if st.session_state.edit_coindata_api_key != coindata.api_key:
            coindata.api_key = st.session_state.edit_coindata_api_key
    if "edit_coindata_fetch_limit" in st.session_state:
        if st.session_state.edit_coindata_fetch_limit != coindata.fetch_limit:
            coindata.fetch_limit = st.session_state.edit_coindata_fetch_limit
    if "edit_coindata_fetch_interval" in st.session_state:
        if st.session_state.edit_coindata_fetch_interval != coindata.fetch_interval:
            coindata.fetch_interval = st.session_state.edit_coindata_fetch_interval
    # Edit
    st.text_input("CoinMarketCap API_Key", value=coindata.api_key, type="password", key="edit_coindata_api_key", help=pbgui_help.coindata_api_key)
    st.number_input("Fetch Limit", min_value=200, max_value=5000, value=coindata.fetch_limit, step=200, format="%.d", key="edit_coindata_fetch_limit", help=pbgui_help.coindata_fetch_limit)
    st.number_input("Fetch Interval", min_value=1, max_value=24, value=coindata.fetch_interval, step=1, format="%.d", key="edit_coindata_fetch_interval", help=pbgui_help.coindata_fetch_interval)

set_page_config("Coin Data")

# Init session states
if is_session_state_initialized():
    st.switch_page("pbgui.py")

if 'setup_coindata' in st.session_state:
    setup_coindata()
else:
    view_coindata()
