import streamlit as st
from pbgui_func import set_page_config
from streamlit_extras.switch_page_button import switch_page
from Optimize import OptimizeItem, OptimizeQueue
from OptimizeConfig import OptimizeConfigs

def opt_edit_config():
    # Display Error
    if "error" in st.session_state:
        st.error(st.session_state.error, icon="🚨")
    # Navigation
    with st.sidebar:
        if st.button(":back:"):
            del st.session_state.opt_edit_config
            st.experimental_rerun()
        my_opt.oc.name = st.text_input('Filename:', value=my_opt.oc.name, max_chars=20, key="opt_config_file_name_input")
        if st.button(":floppy_disk:"):
            my_opt.oc.save()
    # Edit Config
    my_opt.oc.edit()

def opt_edit():
    # Display Error
    if "error" in st.session_state:
        st.error(st.session_state.error, icon="🚨")
    # Navigation
    with st.sidebar:
        if my_opt_config.list():
            config = st.selectbox('Optimize Config',my_opt_config.list(), index = my_opt_config.list().index(my_opt.oc.name))    
            my_opt.oc = my_opt_config.find_config(config)
            my_opt.mode = my_opt.oc.passivbot_mode
            my_opt.algo = my_opt.oc.algorithm
            my_opt.iters = my_opt.oc.iters
        if st.button(f"Edit {my_opt.oc.name}"):
            st.session_state.opt_edit_config = True
            st.experimental_rerun()
        if my_opt.file and my_opt.position >= 0:
           if st.button(":floppy_disk:"):
               my_opt.save(my_opt.position)
        if st.button("Queue"):
            st.session_state.opt_queue = True
            st.experimental_rerun()
    # Create Optimizer GUI
    my_opt.edit_base()
    my_opt.edit_item()
    if st.button("Add to Optimizer Queue"):
        my_opt_queue.add_item(my_opt)
        st.session_state.opt_queue = True
        st.experimental_rerun()

def opt_queue():
    # Display Error
    if "error" in st.session_state:
        st.error(st.session_state.error, icon="🚨")
    # Navigation
    with st.sidebar:
        if st.button(":recycle:"):
            st.experimental_rerun()
        if st.button(":back:"):
            del st.session_state.opt_queue
            my_opt.file = None
            st.experimental_rerun()
    my_opt_queue.options()
    my_opt_queue.view_queue()

set_page_config()

# Init Session State
if 'pbdir' not in st.session_state or 'pbgdir' not in st.session_state:
    switch_page("pbgui")

# Init Optimizer
if 'my_opt' in st.session_state:
    my_opt = st.session_state.my_opt
else:
    my_opt = OptimizeItem()
    st.session_state.my_opt = my_opt

# Init OptimizeConfigs
if 'my_opt_config' in st.session_state:
    my_opt_config = st.session_state.my_opt_config
else:
    my_opt_config = OptimizeConfigs()
    st.session_state.my_opt_config = my_opt_config

# Init Optimizer Queue
if 'my_opt_queue' in st.session_state:
    my_opt_queue = st.session_state.my_opt_queue
else:
    my_opt_queue = OptimizeQueue()
    st.session_state.my_opt_queue = my_opt_queue

if "opt_queue" in st.session_state:
    opt_queue()
elif "opt_edit_config" in st.session_state:
    opt_edit_config()
else:
    opt_edit()
