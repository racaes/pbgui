import streamlit as st
import pbgui_help
import json
import psutil
import sys
import platform
import subprocess
import glob
import configparser
import time
import multiprocessing
from Exchange import Exchange
from pbgui_func import pb7dir, pb7venv, PBGDIR, load_symbols_from_ini, error_popup, info_popup
import uuid
from pathlib import Path, PurePath
from User import Users
import shutil
import datetime
import BacktestV7
from Config import ConfigV7, Bounds
import logging
import os

class OptimizeV7QueueItem:
    def __init__(self):
        self.name = None
        self.filename = None
        self.json = None
        self.exchange = None
        self.starting_config = False
        self.log = None
        self.pid = None
        self.pidfile = None

    def remove(self):
        self.stop()
        file = Path(f'{PBGDIR}/data/opt_v7_queue/{self.filename}.json')
        file.unlink(missing_ok=True)
        self.log.unlink(missing_ok=True)
        self.pidfile.unlink(missing_ok=True)

    def load_log(self, log_size: int = 50):
        if self.log:
            if self.log.exists():
                # Open the file in binary mode to handle raw bytes
                with open(self.log, 'rb') as f:
                    # Move the pointer to the last log_size KB (100 * 1024 bytes)
                    f.seek(0, 2)  # Move to the end of the file
                    file_size = f.tell()
                    # Ensure that we don't try to read more than the file size
                    start_pos = max(file_size - log_size * 1024, 0)
                    f.seek(start_pos)
                    # Read the last 100 KB (or less if the file is smaller)
                    return f.read().decode('utf-8', errors='ignore')  # Decode and ignore errors

    @st.fragment
    def view_log(self):
        col1, col2, col3 = st.columns([1,1,8], vertical_alignment="bottom")
        with col1:
            st.checkbox("Reverse", value=True, key=f'reverse_view_log_{self.filename}')
        with col2:
            st.selectbox("view last kB", [50, 100, 250, 500, 1000, 2000, 5000, 10000, 100000], key=f'size_view_log_{self.filename}')
        with col3:
            if st.button(":material/refresh:", key=f'refresh_view_log_{self.filename}'):
                st.rerun(scope="fragment")
        logfile = self.load_log(st.session_state[f'size_view_log_{self.filename}'])
        if st.session_state[f'reverse_view_log_{self.filename}']:
            logfile = '\n'.join(logfile.split('\n')[::-1])
        with st.container(height=1200):
            st.code(logfile)

    def status(self):
        if self.is_optimizing():
            return "optimizing..."
        if self.is_running():
            return "running"
        if self.is_finish():
            return "complete"
        if self.is_error():
            return "error"
        else:
            return "not started"

    def is_running(self):
        if not self.pid:
            self.load_pid()
        try:
            if self.pid and psutil.pid_exists(self.pid) and any(sub.lower().endswith("optimize.py") for sub in psutil.Process(self.pid).cmdline()):
                return True
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            pass
        return False

    def is_finish(self):
        log = self.load_log()
        if log:
            if "successfully processed optimize_results" in log:
                return True
            else:
                return False
        else:
            return False

    def is_error(self):
        log = self.load_log()
        if log:
            if "successfully processed optimize_results" in log:
                return False
            else:
                return True
        else:
            return False

    def is_optimizing(self):
        if self.is_running():
            log = self.load_log()
            if log:
                if "Optimization complete" in log:
                    return False
                elif "Initial population size" in log:
                    return True
            else:
                return False

    def stop(self):
        if self.is_running():
            parent = psutil.Process(self.pid)
            children = parent.children(recursive=True)
            children.append(parent)
            for p in children:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass


    def load_pid(self):
        if self.pidfile.exists():
            with open(self.pidfile) as f:
                pid = f.read()
                self.pid = int(pid) if pid.isnumeric() else None

    def save_pid(self):
        with open(self.pidfile, 'w') as f:
            f.write(str(self.pid))

    def run(self):
        if not self.is_finish() and not self.is_running():
            old_os_path = os.environ.get('PATH', '')
            new_os_path = os.path.dirname(pb7venv()) + os.pathsep + old_os_path
            os.environ['PATH'] = new_os_path
            if self.starting_config:
                cmd = [pb7venv(), '-u', PurePath(f'{pb7dir()}/src/optimize.py'), '-t', str(PurePath(f'{self.json}')), str(PurePath(f'{self.json}'))]
            else:
                cmd = [pb7venv(), '-u', PurePath(f'{pb7dir()}/src/optimize.py'), str(PurePath(f'{self.json}'))]
            log = open(self.log,"w")
            if platform.system() == "Windows":
                creationflags = subprocess.DETACHED_PROCESS
                creationflags |= subprocess.CREATE_NO_WINDOW
                btm = subprocess.Popen(cmd, stdout=log, stderr=log, cwd=pb7dir(), text=True, creationflags=creationflags)
            else:
                btm = subprocess.Popen(cmd, stdout=log, stderr=log, cwd=pb7dir(), text=True, start_new_session=True)
            self.pid = btm.pid
            self.save_pid()
            os.environ['PATH'] = old_os_path

class OptimizeV7Queue:
    def __init__(self):
        self.items = []
        pb_config = configparser.ConfigParser()
        pb_config.read('pbgui.ini')
        if not pb_config.has_section("optimize_v7"):
            pb_config.add_section("optimize_v7")
            pb_config.set("optimize_v7", "autostart", "False")
            with open('pbgui.ini', 'w') as f:
                pb_config.write(f)
        self._autostart = eval(pb_config.get("optimize_v7", "autostart"))
        if self._autostart:
            self.run()

    @property
    def autostart(self):
        return self._autostart

    @autostart.setter
    def autostart(self, new_autostart):
        self._autostart = new_autostart
        pb_config = configparser.ConfigParser()
        pb_config.read('pbgui.ini')
        pb_config.set("optimize_v7", "autostart", str(self._autostart))
        with open('pbgui.ini', 'w') as f:
            pb_config.write(f)
        if self._autostart:
            self.run()
        else:
            self.stop()

    def add(self, qitem : OptimizeV7QueueItem):
        for index, item in enumerate(self.items):
            if item.filename == qitem.filename:
                return
        self.items.append(qitem)

    def remove_finish(self, all : bool = False):
        if all:
            self.stop()
        for item in self.items[:]:
            if item.is_finish():
                item.remove()
                self.items.remove(item)
            else:
                if all:
                    item.stop()
                    item.remove()
                    self.items.remove(item)
        if self._autostart:
            self.run()

    def remove_selected(self):
        ed_key = st.session_state.ed_key
        ed = st.session_state[f'view_opt_v7_queue_{ed_key}']
        for row in ed["edited_rows"]:
            if "delete" in ed["edited_rows"][row]:
                if ed["edited_rows"][row]["delete"]:
                    self.items[row].remove()
        self.items = []

    def running(self):
        for item in self.items:
            if item.is_running():
                return True
        return False

    def downloading(self):
        for item in self.items:
            if item.is_running() and not item.is_optimizing():
                return True
        return False
        
    def load(self):
        dest = Path(f'{PBGDIR}/data/opt_v7_queue')
        p = str(Path(f'{dest}/*.json'))
        items = glob.glob(p)
        self.items = []
        for item in items:
            with open(item, "r", encoding='utf-8') as f:
                q_config = json.load(f)
                qitem = OptimizeV7QueueItem()
                qitem.name = q_config["name"]
                qitem.filename = q_config["filename"]
                qitem.json = q_config["json"]
                config = OptimizeV7Item(qitem.json)
                qitem.exchange = q_config["exchange"]
                qitem.starting_config = config.config.pbgui.starting_config
                qitem.log = Path(f'{PBGDIR}/data/opt_v7_queue/{qitem.filename}.log')
                qitem.pidfile = Path(f'{PBGDIR}/data/opt_v7_queue/{qitem.filename}.pid')
                self.add(qitem)

    def run(self):
        if not self.is_running():
            cmd = [sys.executable, '-u', PurePath(f'{PBGDIR}/OptimizeV7.py')]
            dest = Path(f'{PBGDIR}/data/logs')
            if not dest.exists():
                dest.mkdir(parents=True)
            logfile = Path(f'{dest}/OptimizeV7.log')
            if logfile.exists():
                if logfile.stat().st_size >= 1048576:
                    logfile.replace(f'{str(logfile)}.old')
            log = open(logfile,"a")
            if platform.system() == "Windows":
                creationflags = subprocess.DETACHED_PROCESS
                creationflags |= subprocess.CREATE_NO_WINDOW
                subprocess.Popen(cmd, stdout=log, stderr=log, cwd=PBGDIR, text=True, creationflags=creationflags)
            else:
                subprocess.Popen(cmd, stdout=log, stderr=log, cwd=PBGDIR, text=True, start_new_session=True)

    def stop(self):
        if self.is_running():
            self.pid().kill()

    def is_running(self):
        if self.pid():
            return True
        return False

    def pid(self):
        for process in psutil.process_iter():
            try:
                cmdline = process.cmdline()
            except psutil.AccessDenied:
                continue
            if any("OptimizeV7.py" in sub for sub in cmdline):
                return process

    def view(self):
        if not self.items:
            self.load()
        if not "ed_key" in st.session_state:
            st.session_state.ed_key = 0
        ed_key = st.session_state.ed_key
        if f'view_opt_v7_queue_{ed_key}' in st.session_state:
            ed = st.session_state[f'view_opt_v7_queue_{ed_key}']
            for row in ed["edited_rows"]:
                if "run" in ed["edited_rows"][row]:
                    if self.items[row].is_running():
                        self.items[row].stop()
                    else:
                        self.items[row].run()
                if "view" in ed["edited_rows"][row]:
                    opt = OptimizeV7Item(f'{PBGDIR}/data/opt_v7/{self.items[row].name}')
                    opt.load()
                    opt.load_results()
                    st.session_state.opt_v7_results = opt
                    del st.session_state.opt_v7_queue
                    st.rerun()
                if "edit" in ed["edited_rows"][row]:
                    st.session_state.opt_v7 = OptimizeV7Item(f'{PBGDIR}/data/opt_v7/{self.items[row].name}.json')
                    del st.session_state.opt_v7_queue
                    st.rerun()
        d = []
        for id, opt in enumerate(self.items):
            d.append({
                'id': id,
                'run': False,
                'Status': opt.status(),
                'edit': False,
                'log': False,
                'delete': False,
                'starting_config': opt.starting_config,
                'name': opt.name,
                'filename': opt.filename,
                'Time': datetime.datetime.fromtimestamp(Path(f'{PBGDIR}/data/opt_v7_queue/{opt.filename}.json').stat().st_mtime),
                'exchange': opt.exchange,
                'finish': opt.is_finish(),
            })
        column_config = {
            # "id": None,
            "run": st.column_config.CheckboxColumn('Start/Stop', default=False),
            "edit": st.column_config.CheckboxColumn('Edit'),
            "log": st.column_config.CheckboxColumn(label="View Logfile"),
            "delete": st.column_config.CheckboxColumn(label="Delete"),
            }
        #Display Queue
        height = 36+(len(d))*35
        if height > 1000: height = 1016
        st.data_editor(data=d, height=height, use_container_width=True, key=f'view_opt_v7_queue_{ed_key}', hide_index=None, column_order=None, column_config=column_config, disabled=['id','filename','starting_config','name','finish','running'])
        if f'view_opt_v7_queue_{ed_key}' in st.session_state:
            ed = st.session_state[f'view_opt_v7_queue_{ed_key}']
            for row in ed["edited_rows"]:
                if "log" in ed["edited_rows"][row]:
                    if ed["edited_rows"][row]["log"]:
                        self.items[row].view_log()

class OptimizeV7Results:
    def __init__(self):
        self.results_path = Path(f'{pb7dir()}/optimize_results')
        self.analysis_path = Path(f'{pb7dir()}/optimize_results_analysis')
        self.results = []
        self.initialize()
    
    def initialize(self):
        self.find_results()
    
    def remove(self, file_name):
        Path(file_name).unlink(missing_ok=True)
        Path(f'{file_name}.bak').unlink(missing_ok=True)
        analysis = PurePath(file_name).stem[0:19]
        analysis = str(self.analysis_path) + f'/{analysis}*.json'
        analysis = glob.glob(analysis, recursive=False)
        if analysis:
            Path(analysis[0]).unlink(missing_ok=True)

    def find_results(self):
        self.results = []
        if self.results_path.exists():
            p = str(self.results_path) + "/*.txt"
            self.results = glob.glob(p, recursive=False)

    def find_result_name(self, result_file):
        with open(result_file, "r", encoding='utf-8') as f:
            first_line = f.readline()
            config = json.loads(first_line)
            backtest_name = config["config"]["backtest"]["base_dir"].split("/")[-1]
            return backtest_name

    def view_analysis(self, analysis):
        file = Path(f'{self.analysis_path}/{analysis}.json')
        with open(file, "r", encoding='utf-8') as f:
            config = json.load(f)
            st.code(json.dumps(config, indent=4))

    def view_results(self):
        # Init
        if not "ed_key" in st.session_state:
            st.session_state.ed_key = 0
        ed_key = st.session_state.ed_key
        if not "opt_v7_results_d" in st.session_state:
            d = []
            for id, opt in enumerate(self.results):
                name = self.find_result_name(opt)
                analysis = PurePath(opt).stem[0:19]
                analysis = str(self.analysis_path) + f'/{analysis}*.json'
                analysis = glob.glob(analysis, recursive=False)
                analysis = analysis[0] if analysis else None
                result = PurePath(opt).stem
                result_time = Path(opt).stat().st_mtime
                if analysis:
                    analysis_time = Path(analysis).stat().st_mtime
                    analysis = PurePath(analysis).stem
                d.append({
                    'id': id,
                    'Name': name,
                    'Result': result,
                    'Result Time': datetime.datetime.fromtimestamp(result_time),
                    'Analysis': analysis,
                    'Analysis Time': datetime.datetime.fromtimestamp(analysis_time) if analysis else None,
                    'view': False,
                    "generate": False,
                    'backtest': False,
                    'delete' : False,
                })
            st.session_state.opt_v7_results_d = d
        d = st.session_state.opt_v7_results_d
        column_config = {
            "id": None,
            "edit": st.column_config.CheckboxColumn(label="Edit"),
            "view": st.column_config.CheckboxColumn(label="View Analysis"),
            "generate": st.column_config.CheckboxColumn(label="Generate Analysis"),
            "backtest": st.column_config.CheckboxColumn(label="Backtest"),
            "Result Time": st.column_config.DateColumn(format="YYYY-MM-DD HH:mm:ss"),
            "Analysis Time": st.column_config.DateColumn(format="YYYY-MM-DD HH:mm:ss"),
            }
        #Display optimizes
        st.data_editor(data=d, height=36+(len(d))*35, use_container_width=True, key=f'select_optresults_{ed_key}', hide_index=None, column_order=None, column_config=column_config, disabled=['id','name'])
        if f'select_optresults_{ed_key}' in st.session_state:
            ed = st.session_state[f'select_optresults_{ed_key}']
            for row in ed["edited_rows"]:
                if "view" in ed["edited_rows"][row]:
                    if ed["edited_rows"][row]["view"]:
                        if d[row]["Analysis"]:
                            self.view_analysis(d[row]["Analysis"])
                if "generate" in ed["edited_rows"][row]:
                    if ed["edited_rows"][row]["generate"]:
                        result_name = PurePath(f'{self.results_path}/{d[row]["Result"]}.txt')
                        self.generate_analysis(result_name)
                        st.session_state.ed_key += 1
                        if "opt_v7_results_d" in st.session_state:
                            del st.session_state.opt_v7_results_d
                        # st.rerun()
                if "backtest" in ed["edited_rows"][row]:
                    if ed["edited_rows"][row]["backtest"]:
                        backtest_name = PurePath(f'{self.analysis_path}/{d[row]["Analysis"]}.json')
                        st.session_state.bt_v7 = BacktestV7.BacktestV7Item(backtest_name)
                        if "bt_v7_queue" in st.session_state:
                            del st.session_state.bt_v7_queue
                        if "bt_v7_results" in st.session_state:
                            del st.session_state.bt_v7_results
                        if "bt_v7_edit_symbol" in st.session_state:
                            del st.session_state.bt_v7_edit_symbol
                        st.switch_page("pages/71_V7 Backtest.py")

    def generate_analysis(self, result_file):
        # create a copy of result_file
        result_file = Path(result_file)
        result_file_copy = Path(f'{result_file}.bak')
        shutil.copy(result_file, result_file_copy)
        # run extract_best_config.py on result_file_copy
        cmd = [pb7venv(), '-u', PurePath(f'{pb7dir()}/src/tools/extract_best_config.py'), str(result_file_copy)]
        with st.spinner('Generating Result...'):
            if platform.system() == "Windows":
                creationflags = subprocess.CREATE_NO_WINDOW
                result = subprocess.run(cmd, capture_output=True, cwd=pb7dir(), text=True, creationflags=creationflags)
            else:
                result = subprocess.run(cmd, capture_output=True, cwd=pb7dir(), text=True, start_new_session=True)
        if "error" in result.stdout:
            error_popup(result.stdout)
        else:
            info_popup(f"Analysis Generated {result.stdout}")

    def remove_selected_results(self):
        ed_key = st.session_state.ed_key
        ed = st.session_state[f'select_optresults_{ed_key}']
        for row in ed["edited_rows"]:
            if "delete" in ed["edited_rows"][row]:
                if ed["edited_rows"][row]["delete"]:
                    self.remove(self.results[row])
        self.results = []
        self.find_results()
    
    def remove_all_results(self):
        shutil.rmtree(self.results_path, ignore_errors=True)
        shutil.rmtree(self.analysis_path, ignore_errors=True)
        self.results = []

class OptimizeV7Item:
    def __init__(self, optimize_file: str = None):
        self.name = ""
        self.log = None
        self.config = ConfigV7()
        self.users = Users()
        self.backtest_results = []
        if optimize_file:
            self.name = PurePath(optimize_file).stem
            self.config.config_file = optimize_file
            self.config.load_config()
        else:
            self.initialize()

    def initialize(self):
        self.config.backtest.start_date = (datetime.date.today() - datetime.timedelta(days=365*4)).strftime("%Y-%m-%d")
        self.config.backtest.end_date = datetime.date.today().strftime("%Y-%m-%d")
        self.config.optimize.n_cpus = multiprocessing.cpu_count()

    def edit(self):
        # Init coindata
        coindata = st.session_state.pbcoindata
        if coindata.exchange != self.config.backtest.exchange:
            coindata.exchange = self.config.backtest.exchange
        if coindata.market_cap != self.config.pbgui.market_cap:
            coindata.market_cap = self.config.pbgui.market_cap
        if coindata.vol_mcap != self.config.pbgui.vol_mcap:
            coindata.vol_mcap = self.config.pbgui.vol_mcap
        # Init session_state for keys
        if "edit_opt_v7_exchange" in st.session_state:
            if st.session_state.edit_opt_v7_exchange != self.config.backtest.exchange:
                self.config.backtest.exchange = st.session_state.edit_opt_v7_exchange
                coindata.exchange = self.config.backtest.exchange
        if "edit_opt_v7_name" in st.session_state:
            if st.session_state.edit_opt_v7_name != self.name:
                self.name = st.session_state.edit_opt_v7_name
                self.config.backtest.base_dir = f'backtests/pbgui/{self.name}'
        if "edit_opt_v7_sd" in st.session_state:
            if st.session_state.edit_opt_v7_sd.strftime("%Y-%m-%d") != self.config.backtest.start_date:
                self.config.backtest.start_date = st.session_state.edit_opt_v7_sd.strftime("%Y-%m-%d")
        if "edit_opt_v7_ed" in st.session_state:
            if st.session_state.edit_opt_v7_ed.strftime("%Y-%m-%d") != self.config.backtest.end_date:
                self.config.backtest.end_date = st.session_state.edit_opt_v7_ed.strftime("%Y-%m-%d")
        if "edit_opt_v7_sb" in st.session_state:
            if st.session_state.edit_opt_v7_sb != self.config.backtest.starting_balance:
                self.config.backtest.starting_balance = st.session_state.edit_opt_v7_sb
        if  "edit_opt_v7_iters" in st.session_state:
            if st.session_state.edit_opt_v7_iters != self.config.optimize.iters:
                self.config.optimize.iters = st.session_state.edit_opt_v7_iters
        if  "edit_opt_v7_n_cpu" in st.session_state:
            if st.session_state.edit_opt_v7_n_cpu != self.config.optimize.n_cpus:
                self.config.optimize.n_cpus = st.session_state.edit_opt_v7_n_cpu
        if "edit_opt_v7_starting_config" in st.session_state:
            if st.session_state.edit_opt_v7_starting_config != self.config.pbgui.starting_config:
                self.config.pbgui.starting_config = st.session_state.edit_opt_v7_starting_config
        if "edit_opt_v7_lower_bound_drawdown_worst" in st.session_state:
            if st.session_state.edit_opt_v7_lower_bound_drawdown_worst != self.config.optimize.limits.lower_bound_drawdown_worst:
                self.config.optimize.limits.lower_bound_drawdown_worst = st.session_state.edit_opt_v7_lower_bound_drawdown_worst
        if "edit_opt_v7_lower_bound_equity_balance_diff_mean" in st.session_state:
            if st.session_state.edit_opt_v7_lower_bound_equity_balance_diff_mean != self.config.optimize.limits.lower_bound_equity_balance_diff_mean:
                self.config.optimize.limits.lower_bound_equity_balance_diff_mean = st.session_state.edit_opt_v7_lower_bound_equity_balance_diff_mean
        if "edit_opt_v7_lower_bound_loss_profit_ratio" in st.session_state:
            if st.session_state.edit_opt_v7_lower_bound_loss_profit_ratio != self.config.optimize.limits.lower_bound_loss_profit_ratio:
                self.config.optimize.limits.lower_bound_loss_profit_ratio = st.session_state.edit_opt_v7_lower_bound_loss_profit_ratio
        if "edit_opt_v7_population_size" in st.session_state:
            if st.session_state.edit_opt_v7_population_size != self.config.optimize.population_size:
                self.config.optimize.population_size = st.session_state.edit_opt_v7_population_size
        if "edit_opt_v7_crossover_probability" in st.session_state:
            if st.session_state.edit_opt_v7_crossover_probability != self.config.optimize.crossover_probability:
                self.config.optimize.crossover_probability = st.session_state.edit_opt_v7_crossover_probability
        if "edit_opt_v7_mutation_probability" in st.session_state:
            if st.session_state.edit_opt_v7_mutation_probability != self.config.optimize.mutation_probability:
                self.config.optimize.mutation_probability = st.session_state.edit_opt_v7_mutation_probability
        if "edit_opt_v7_scoring" in st.session_state:
            if st.session_state.edit_opt_v7_scoring != self.config.optimize.scoring:
                self.config.optimize.scoring = st.session_state.edit_opt_v7_scoring
        # Filters
        if "edit_opt_v7_market_cap" in st.session_state:
            if st.session_state.edit_opt_v7_market_cap != self.config.pbgui.market_cap:
                self.config.pbgui.market_cap = st.session_state.edit_opt_v7_market_cap
                coindata.market_cap = self.config.pbgui.market_cap
        if "edit_opt_v7_vol_mcap" in st.session_state:
            if st.session_state.edit_opt_v7_vol_mcap != self.config.pbgui.vol_mcap:
                self.config.pbgui.vol_mcap = st.session_state.edit_opt_v7_vol_mcap
                coindata.vol_mcap = self.config.pbgui.vol_mcap
        # Symbol config
        if "edit_opt_v7_approved_coins_long" in st.session_state:
            if st.session_state.edit_opt_v7_approved_coins_long != self.config.live.approved_coins.long:
                self.config.live.approved_coins.long = st.session_state.edit_opt_v7_approved_coins_long
                if 'All' in self.config.live.approved_coins.long:
                    self.config.live.approved_coins.long = coindata.symbols.copy()
                elif 'CPT' in self.config.live.approved_coins.long:
                    self.config.live.approved_coins.long = coindata.symbols_cpt.copy()
        if "edit_opt_v7_approved_coins_short" in st.session_state:
            if st.session_state.edit_opt_v7_approved_coins_short != self.config.live.approved_coins.short:
                self.config.live.approved_coins.short = st.session_state.edit_opt_v7_approved_coins_short
                if 'All' in self.config.live.approved_coins.short:
                    self.config.live.approved_coins.short = coindata.symbols.copy()
                elif 'CPT' in self.config.live.approved_coins.short:
                    self.config.live.approved_coins.short = coindata.symbols_cpt.copy()
        # Display Editor
        col1, col2, col3, col4 = st.columns([1,1,1,1])
        with col1:
            exchange_index = 0
            if self.config.backtest.exchange == "bybit":
                exchange_index = 1
            st.selectbox('Exchange',['binance', 'bybit'], index = exchange_index, key="edit_opt_v7_exchange")
        with col2:
            if not self.name:
                color = "red"
            else:
                color = None
            if color:
                st.text_input(f":{color}[Optimize Name]", value=self.name, max_chars=64, key="edit_opt_v7_name")
            else:
                st.text_input("Optimize Name", value=self.name, max_chars=64, key="edit_opt_v7_name")
        with col3:
            st.date_input("START_DATE", datetime.datetime.strptime(self.config.backtest.start_date, '%Y-%m-%d'), format="YYYY-MM-DD", key="edit_opt_v7_sd")
        with col4:
            st.date_input("END_DATE", datetime.datetime.strptime(self.config.backtest.end_date, '%Y-%m-%d'), format="YYYY-MM-DD", key="edit_opt_v7_ed")
        col1, col2, col3, col4 = st.columns([1,1,1,1])
        with col1:
            st.number_input('STARTING_BALANCE',value=float(self.config.backtest.starting_balance),step=500.0, key="edit_opt_v7_sb")
        with col2:
            st.number_input('iters',value=self.config.optimize.iters, step=1000, help=pbgui_help.opt_iters, key="edit_opt_v7_iters")
        with col3:
            st.number_input('n_cpus',value=self.config.optimize.n_cpus, min_value=1, max_value=multiprocessing.cpu_count(), step=1, help=None, key="edit_opt_v7_n_cpu")
        with col4:
            st.checkbox("Start with config", value=self.config.pbgui.starting_config, key="edit_opt_v7_starting_config")
        with st.expander("Edit Config", expanded=False):
            self.config.bot.edit()
        col1, col2, col3, col4 = st.columns([1,1,1,1], vertical_alignment="bottom")
        with col1:
            st.number_input("lower_bound_drawdown_worst", min_value=0.0, max_value=1.0, value=self.config.optimize.limits.lower_bound_drawdown_worst, step=0.01, format="%.2f", key="edit_opt_v7_lower_bound_drawdown_worst", help=pbgui_help.limits_lower_bound_drawdown_worst)
        with col2:
            st.number_input("lower_bound_equity_balance_diff_mean", min_value=0.0, max_value=1.0, value=self.config.optimize.limits.lower_bound_equity_balance_diff_mean, step=0.01, format="%.2f", key="edit_opt_v7_lower_bound_equity_balance_diff_mean", help=pbgui_help.limits_lower_bound_equity_balance_diff_mean)
        with col3:
            st.number_input("lower_bound_loss_profit_ratio", min_value=0.0, max_value=1.0, value=self.config.optimize.limits.lower_bound_loss_profit_ratio, step=0.01, format="%.2f", key="edit_opt_v7_lower_bound_loss_profit_ratio", help=pbgui_help.limits_lower_bound_loss_profit_ratio)
        with col4:
            st.multiselect("scoring", ["mdg", "sharpe_ratio"], default=self.config.optimize.scoring, key="edit_opt_v7_scoring", help=pbgui_help.scoring)
        col1, col2, col3, col4 = st.columns([1,1,1,1])
        with col1:
            st.number_input("population_size", min_value=1, max_value=10000, value=self.config.optimize.population_size, step=1, format="%d", key="edit_opt_v7_population_size", help=pbgui_help.population_size)
        with col2:
            st.number_input("crossover_probability", min_value=0.0, max_value=1.0, value=self.config.optimize.crossover_probability, step=0.01, format="%.2f", key="edit_opt_v7_crossover_probability", help=pbgui_help.crossover_probability)
        with col3:
            st.number_input("mutation_probability", min_value=0.0, max_value=1.0, value=self.config.optimize.mutation_probability, step=0.01, format="%.2f", key="edit_opt_v7_mutation_probability", help=pbgui_help.mutation_probability)
        with col4:
            st.empty()
        # Filters
        col1, col2, col3, col4 = st.columns([1,1,1,1])
        with col1:
            st.number_input("market_cap", min_value=0, value=self.config.pbgui.market_cap, step=50, format="%.d", key="edit_opt_v7_market_cap", help=pbgui_help.market_cap)
        with col2:
            st.number_input("vol/mcap", min_value=0.0, value=round(float(self.config.pbgui.vol_mcap),2), step=0.05, format="%.2f", key="edit_opt_v7_vol_mcap", help=pbgui_help.vol_mcap)
        # Apply filters
        for symbol in coindata.ignored_coins:
            if symbol in self.config.live.approved_coins.long:
                self.config.live.approved_coins.long.remove(symbol)
            if symbol in self.config.live.approved_coins.short:
                self.config.live.approved_coins.short.remove(symbol)
        # Remove unavailable symbols
        for symbol in self.config.live.approved_coins.long.copy():
            if symbol not in coindata.symbols:
                self.config.live.approved_coins.long.remove(symbol)
        for symbol in self.config.live.approved_coins.short.copy():
            if symbol not in coindata.symbols:
                self.config.live.approved_coins.short.remove(symbol)
        # Correct Display of Symbols
        if "edit_run_v7_approved_coins_long" in st.session_state:
            st.session_state.edit_run_v7_approved_coins_long = self.config.live.approved_coins.long
        if "edit_run_v7_approved_coins_short" in st.session_state:
            st.session_state.edit_run_v7_approved_coins_short = self.config.live.approved_coins.short
        col1, col2 = st.columns([1,1], vertical_alignment="bottom")
        with col1:
            st.multiselect('approved_coins_long', ['All', 'CPT'] + coindata.symbols, default=self.config.live.approved_coins.long, key="edit_opt_v7_approved_coins_long")
        with col2:
            st.multiselect('approved_coins_short', ['All', 'CPT'] + coindata.symbols, default=self.config.live.approved_coins.short, key="edit_opt_v7_approved_coins_short")
        col1, col2, col3, col4 = st.columns([1,1,1,1])
        with col1:
            # long_close_grid_markup_range
            if "edit_opt_v7_long_close_grid_markup_range_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_close_grid_markup_range_0 != self.config.optimize.bounds.long_close_grid_markup_range_0:
                    self.config.optimize.bounds.long_close_grid_markup_range_0 = st.session_state.edit_opt_v7_long_close_grid_markup_range_0
            if "edit_opt_v7_long_close_grid_markup_range_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_close_grid_markup_range_1 != self.config.optimize.bounds.long_close_grid_markup_range_1:
                    self.config.optimize.bounds.long_close_grid_markup_range_1 = st.session_state.edit_opt_v7_long_close_grid_markup_range_1
            # long_close_grid_min_markup_0
            if "edit_opt_v7_long_close_grid_min_markup_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_close_grid_min_markup_0 != self.config.optimize.bounds.long_close_grid_min_markup_0:
                    self.config.optimize.bounds.long_close_grid_min_markup_0 = st.session_state.edit_opt_v7_long_close_grid_min_markup_0
            if "edit_opt_v7_long_close_grid_min_markup_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_close_grid_min_markup_1 != self.config.optimize.bounds.long_close_grid_min_markup_1:
                    self.config.optimize.bounds.long_close_grid_min_markup_1 = st.session_state.edit_opt_v7_long_close_grid_min_markup_1
            # long_close_grid_qty_pct_0
            if "edit_opt_v7_long_close_grid_qty_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_close_grid_qty_pct_0 != self.config.optimize.bounds.long_close_grid_qty_pct_0:
                    self.config.optimize.bounds.long_close_grid_qty_pct_0 = st.session_state.edit_opt_v7_long_close_grid_qty_pct_0
            if "edit_opt_v7_long_close_grid_qty_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_close_grid_qty_pct_1 != self.config.optimize.bounds.long_close_grid_qty_pct_1:
                    self.config.optimize.bounds.long_close_grid_qty_pct_1 = st.session_state.edit_opt_v7_long_close_grid_qty_pct_1
            # long_close_trailing_grid_ratio_0
            if "edit_opt_v7_long_close_trailing_grid_ratio_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_close_trailing_grid_ratio_0 != self.config.optimize.bounds.long_close_trailing_grid_ratio_0:
                    self.config.optimize.bounds.long_close_trailing_grid_ratio_0 = st.session_state.edit_opt_v7_long_close_trailing_grid_ratio_0
            if "edit_opt_v7_long_close_trailing_grid_ratio_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_close_trailing_grid_ratio_1 != self.config.optimize.bounds.long_close_trailing_grid_ratio_1:
                    self.config.optimize.bounds.long_close_trailing_grid_ratio_1 = st.session_state.edit_opt_v7_long_close_trailing_grid_ratio_1
            # long_close_trailing_qty_pct_0
            if "edit_opt_v7_long_close_trailing_qty_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_close_trailing_qty_pct_0 != self.config.optimize.bounds.long_close_trailing_qty_pct_0:
                    self.config.optimize.bounds.long_close_trailing_qty_pct_0 = st.session_state.edit_opt_v7_long_close_trailing_qty_pct_0
            if "edit_opt_v7_long_close_trailing_qty_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_close_trailing_qty_pct_1 != self.config.optimize.bounds.long_close_trailing_qty_pct_1:
                    self.config.optimize.bounds.long_close_trailing_qty_pct_1 = st.session_state.edit_opt_v7_long_close_trailing_qty_pct_1
            # long_close_trailing_retracement_pct_0
            if "edit_opt_v7_long_close_trailing_retracement_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_close_trailing_retracement_pct_0 != self.config.optimize.bounds.long_close_trailing_retracement_pct_0:
                    self.config.optimize.bounds.long_close_trailing_retracement_pct_0 = st.session_state.edit_opt_v7_long_close_trailing_retracement_pct_0
            if "edit_opt_v7_long_close_trailing_retracement_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_close_trailing_retracement_pct_1 != self.config.optimize.bounds.long_close_trailing_retracement_pct_1:
                    self.config.optimize.bounds.long_close_trailing_retracement_pct_1 = st.session_state.edit_opt_v7_long_close_trailing_retracement_pct_1
            # long_close_trailing_threshold_pct_0
            if "edit_opt_v7_long_close_trailing_threshold_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_close_trailing_threshold_pct_0 != self.config.optimize.bounds.long_close_trailing_threshold_pct_0:
                    self.config.optimize.bounds.long_close_trailing_threshold_pct_0 = st.session_state.edit_opt_v7_long_close_trailing_threshold_pct_0
            if "edit_opt_v7_long_close_trailing_threshold_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_close_trailing_threshold_pct_1 != self.config.optimize.bounds.long_close_trailing_threshold_pct_1:
                    self.config.optimize.bounds.long_close_trailing_threshold_pct_1 = st.session_state.edit_opt_v7_long_close_trailing_threshold_pct_1
            # long_ema_span_0_0
            if "edit_opt_v7_long_ema_span_0_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_ema_span_0_0 != self.config.optimize.bounds.long_ema_span_0_0:
                    self.config.optimize.bounds.long_ema_span_0_0 = st.session_state.edit_opt_v7_long_ema_span_0_0
            if "edit_opt_v7_long_ema_span_0_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_ema_span_0_1 != self.config.optimize.bounds.long_ema_span_0_1:
                    self.config.optimize.bounds.long_ema_span_0_1 = st.session_state.edit_opt_v7_long_ema_span_0_1
            # long_ema_span_1_0
            if "edit_opt_v7_long_ema_span_1_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_ema_span_1_0 != self.config.optimize.bounds.long_ema_span_1_0:
                    self.config.optimize.bounds.long_ema_span_1_0 = st.session_state.edit_opt_v7_long_ema_span_1_0
            if "edit_opt_v7_long_ema_span_1_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_ema_span_1_1 != self.config.optimize.bounds.long_ema_span_1_1:
                    self.config.optimize.bounds.long_ema_span_1_1 = st.session_state.edit_opt_v7_long_ema_span_1_1
            # long_entry_grid_double_down_factor_0
            if "edit_opt_v7_long_entry_grid_double_down_factor_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_entry_grid_double_down_factor_0 != self.config.optimize.bounds.long_entry_grid_double_down_factor_0:
                    self.config.optimize.bounds.long_entry_grid_double_down_factor_0 = st.session_state.edit_opt_v7_long_entry_grid_double_down_factor_0
            if "edit_opt_v7_long_entry_grid_double_down_factor_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_entry_grid_double_down_factor_1 != self.config.optimize.bounds.long_entry_grid_double_down_factor_1:
                    self.config.optimize.bounds.long_entry_grid_double_down_factor_1 = st.session_state.edit_opt_v7_long_entry_grid_double_down_factor_1
            # long_entry_grid_spacing_pct_0
            if "edit_opt_v7_long_entry_grid_spacing_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_entry_grid_spacing_pct_0 != self.config.optimize.bounds.long_entry_grid_spacing_pct_0:
                    self.config.optimize.bounds.long_entry_grid_spacing_pct_0 = st.session_state.edit_opt_v7_long_entry_grid_spacing_pct_0
            if "edit_opt_v7_long_entry_grid_spacing_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_entry_grid_spacing_pct_1 != self.config.optimize.bounds.long_entry_grid_spacing_pct_1:
                    self.config.optimize.bounds.long_entry_grid_spacing_pct_1 = st.session_state.edit_opt_v7_long_entry_grid_spacing_pct_1
            # long_entry_grid_spacing_weight_0
            if "edit_opt_v7_long_entry_grid_spacing_weight_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_entry_grid_spacing_weight_0 != self.config.optimize.bounds.long_entry_grid_spacing_weight_0:
                    self.config.optimize.bounds.long_entry_grid_spacing_weight_0 = st.session_state.edit_opt_v7_long_entry_grid_spacing_weight_0
            if "edit_opt_v7_long_entry_grid_spacing_weight_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_entry_grid_spacing_weight_1 != self.config.optimize.bounds.long_entry_grid_spacing_weight_1:
                    self.config.optimize.bounds.long_entry_grid_spacing_weight_1 = st.session_state.edit_opt_v7_long_entry_grid_spacing_weight_1
            # long_entry_initial_ema_dist_0
            if "edit_opt_v7_long_entry_initial_ema_dist_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_entry_initial_ema_dist_0 != self.config.optimize.bounds.long_entry_initial_ema_dist_0:
                    self.config.optimize.bounds.long_entry_initial_ema_dist_0 = st.session_state.edit_opt_v7_long_entry_initial_ema_dist_0
            if "edit_opt_v7_long_entry_initial_ema_dist_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_entry_initial_ema_dist_1 != self.config.optimize.bounds.long_entry_initial_ema_dist_1:
                    self.config.optimize.bounds.long_entry_initial_ema_dist_1 = st.session_state.edit_opt_v7_long_entry_initial_ema_dist_1
            # long_entry_initial_qty_pct_0
            if "edit_opt_v7_long_entry_initial_qty_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_entry_initial_qty_pct_0 != self.config.optimize.bounds.long_entry_initial_qty_pct_0:
                    self.config.optimize.bounds.long_entry_initial_qty_pct_0 = st.session_state.edit_opt_v7_long_entry_initial_qty_pct_0
            if "edit_opt_v7_long_entry_initial_qty_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_entry_initial_qty_pct_1 != self.config.optimize.bounds.long_entry_initial_qty_pct_1:
                    self.config.optimize.bounds.long_entry_initial_qty_pct_1 = st.session_state.edit_opt_v7_long_entry_initial_qty_pct_1
            # long_entry_trailing_grid_ratio_0
            if "edit_opt_v7_long_entry_trailing_grid_ratio_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_entry_trailing_grid_ratio_0 != self.config.optimize.bounds.long_entry_trailing_grid_ratio_0:
                    self.config.optimize.bounds.long_entry_trailing_grid_ratio_0 = st.session_state.edit_opt_v7_long_entry_trailing_grid_ratio_0
            if "edit_opt_v7_long_entry_trailing_grid_ratio_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_entry_trailing_grid_ratio_1 != self.config.optimize.bounds.long_entry_trailing_grid_ratio_1:
                    self.config.optimize.bounds.long_entry_trailing_grid_ratio_1 = st.session_state.edit_opt_v7_long_entry_trailing_grid_ratio_1
            # long_entry_trailing_retracement_pct_0
            if "edit_opt_v7_long_entry_trailing_retracement_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_entry_trailing_retracement_pct_0 != self.config.optimize.bounds.long_entry_trailing_retracement_pct_0:
                    self.config.optimize.bounds.long_entry_trailing_retracement_pct_0 = st.session_state.edit_opt_v7_long_entry_trailing_retracement_pct_0
            if "edit_opt_v7_long_entry_trailing_retracement_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_entry_trailing_retracement_pct_1 != self.config.optimize.bounds.long_entry_trailing_retracement_pct_1:
                    self.config.optimize.bounds.long_entry_trailing_retracement_pct_1 = st.session_state.edit_opt_v7_long_entry_trailing_retracement_pct_1
            # long_entry_trailing_threshold_pct_0
            if "edit_opt_v7_long_entry_trailing_threshold_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_entry_trailing_threshold_pct_0 != self.config.optimize.bounds.long_entry_trailing_threshold_pct_0:
                    self.config.optimize.bounds.long_entry_trailing_threshold_pct_0 = st.session_state.edit_opt_v7_long_entry_trailing_threshold_pct_0
            if "edit_opt_v7_long_entry_trailing_threshold_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_entry_trailing_threshold_pct_1 != self.config.optimize.bounds.long_entry_trailing_threshold_pct_1:
                    self.config.optimize.bounds.long_entry_trailing_threshold_pct_1 = st.session_state.edit_opt_v7_long_entry_trailing_threshold_pct_1
            # long_filter_relative_volume_clip_pct_0
            if "edit_opt_v7_long_filter_relative_volume_clip_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_filter_relative_volume_clip_pct_0 != self.config.optimize.bounds.long_filter_relative_volume_clip_pct_0:
                    self.config.optimize.bounds.long_filter_relative_volume_clip_pct_0 = st.session_state.edit_opt_v7_long_filter_relative_volume_clip_pct_0
            if "edit_opt_v7_long_filter_relative_volume_clip_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_filter_relative_volume_clip_pct_1 != self.config.optimize.bounds.long_filter_relative_volume_clip_pct_1:
                    self.config.optimize.bounds.long_filter_relative_volume_clip_pct_1 = st.session_state.edit_opt_v7_long_filter_relative_volume_clip_pct_1
            # long_filter_rolling_window_0
            if "edit_opt_v7_long_filter_rolling_window_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_filter_rolling_window_0 != self.config.optimize.bounds.long_filter_rolling_window_0:
                    self.config.optimize.bounds.long_filter_rolling_window_0 = st.session_state.edit_opt_v7_long_filter_rolling_window_0
            if "edit_opt_v7_long_filter_rolling_window_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_filter_rolling_window_1 != self.config.optimize.bounds.long_filter_rolling_window_1:
                    self.config.optimize.bounds.long_filter_rolling_window_1 = st.session_state.edit_opt_v7_long_filter_rolling_window_1
            # long_n_positions_0
            if "edit_opt_v7_long_n_positions_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_n_positions_0 != self.config.optimize.bounds.long_n_positions_0:
                    self.config.optimize.bounds.long_n_positions_0 = st.session_state.edit_opt_v7_long_n_positions_0
            if "edit_opt_v7_long_n_positions_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_n_positions_1 != self.config.optimize.bounds.long_n_positions_1:
                    self.config.optimize.bounds.long_n_positions_1 = st.session_state.edit_opt_v7_long_n_positions_1
            # long_total_wallet_exposure_limit_0 
            if "edit_opt_v7_long_total_wallet_exposure_limit_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_total_wallet_exposure_limit_0 != self.config.optimize.bounds.long_total_wallet_exposure_limit_0:
                    self.config.optimize.bounds.long_total_wallet_exposure_limit_0 = st.session_state.edit_opt_v7_long_total_wallet_exposure_limit_0
            if "edit_opt_v7_long_total_wallet_exposure_limit_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_total_wallet_exposure_limit_1 != self.config.optimize.bounds.long_total_wallet_exposure_limit_1:
                    self.config.optimize.bounds.long_total_wallet_exposure_limit_1 = st.session_state.edit_opt_v7_long_total_wallet_exposure_limit_1
            # long_unstuck_close_pct_0
            if "edit_opt_v7_long_unstuck_close_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_unstuck_close_pct_0 != self.config.optimize.bounds.long_unstuck_close_pct_0:
                    self.config.optimize.bounds.long_unstuck_close_pct_0 = st.session_state.edit_opt_v7_long_unstuck_close_pct_0
            if "edit_opt_v7_long_unstuck_close_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_unstuck_close_pct_1 != self.config.optimize.bounds.long_unstuck_close_pct_1:
                    self.config.optimize.bounds.long_unstuck_close_pct_1 = st.session_state.edit_opt_v7_long_unstuck_close_pct_1
            # long_unstuck_ema_dist_0
            if "edit_opt_v7_long_unstuck_ema_dist_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_unstuck_ema_dist_0 != self.config.optimize.bounds.long_unstuck_ema_dist_0:
                    self.config.optimize.bounds.long_unstuck_ema_dist_0 = st.session_state.edit_opt_v7_long_unstuck_ema_dist_0
            if "edit_opt_v7_long_unstuck_ema_dist_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_unstuck_ema_dist_1 != self.config.optimize.bounds.long_unstuck_ema_dist_1:
                    self.config.optimize.bounds.long_unstuck_ema_dist_1 = st.session_state.edit_opt_v7_long_unstuck_ema_dist_1
            # long_unstuck_loss_allowance_pct_0
            if "edit_opt_v7_long_unstuck_loss_allowance_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_unstuck_loss_allowance_pct_0 != self.config.optimize.bounds.long_unstuck_loss_allowance_pct_0:
                    self.config.optimize.bounds.long_unstuck_loss_allowance_pct_0 = st.session_state.edit_opt_v7_long_unstuck_loss_allowance_pct_0
            if "edit_opt_v7_long_unstuck_loss_allowance_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_unstuck_loss_allowance_pct_1 != self.config.optimize.bounds.long_unstuck_loss_allowance_pct_1:
                    self.config.optimize.bounds.long_unstuck_loss_allowance_pct_1 = st.session_state.edit_opt_v7_long_unstuck_loss_allowance_pct_1
            # long_unstuck_threshold_0
            if "edit_opt_v7_long_unstuck_threshold_0" in st.session_state:
                if st.session_state.edit_opt_v7_long_unstuck_threshold_0 != self.config.optimize.bounds.long_unstuck_threshold_0:
                    self.config.optimize.bounds.long_unstuck_threshold_0 = st.session_state.edit_opt_v7_long_unstuck_threshold_0
            if "edit_opt_v7_long_unstuck_threshold_1" in st.session_state:
                if st.session_state.edit_opt_v7_long_unstuck_threshold_1 != self.config.optimize.bounds.long_unstuck_threshold_1:
                    self.config.optimize.bounds.long_unstuck_threshold_1 = st.session_state.edit_opt_v7_long_unstuck_threshold_1
            st.number_input("long_close_grid_markup_range min", min_value=Bounds.CLOSE_GRID_MARKUP_RANGE_MIN, max_value=float(round(self.config.optimize.bounds.long_close_grid_markup_range_1, Bounds.CLOSE_GRID_MARKUP_RANGE_ROUND)), value=float(round(self.config.optimize.bounds.long_close_grid_markup_range_0, Bounds.CLOSE_GRID_MARKUP_RANGE_ROUND)), step=Bounds.CLOSE_GRID_MARKUP_RANGE_STEP, format=Bounds.CLOSE_GRID_MARKUP_RANGE_FORMAT, key="edit_opt_v7_long_close_grid_markup_range_0", help=pbgui_help.close_grid_parameters)
            st.number_input("long_close_grid_min_markup min", min_value=Bounds.CLOSE_GRID_MIN_MARKUP_MIN, max_value=float(round(self.config.optimize.bounds.long_close_grid_min_markup_1, Bounds.CLOSE_GRID_MIN_MARKUP_ROUND)), value=float(round(self.config.optimize.bounds.long_close_grid_min_markup_0, Bounds.CLOSE_GRID_MIN_MARKUP_ROUND)), step=Bounds.CLOSE_GRID_MIN_MARKUP_STEP, format=Bounds.CLOSE_GRID_MIN_MARKUP_FORMAT, key="edit_opt_v7_long_close_grid_min_markup_0", help=pbgui_help.close_grid_parameters)
            st.number_input("long_close_grid_qty_pct min", min_value=Bounds.CLOSE_GRID_QTY_PCT_MIN, max_value=float(round(self.config.optimize.bounds.long_close_grid_qty_pct_1, Bounds.CLOSE_GRID_QTY_PCT_ROUND)), value=float(round(self.config.optimize.bounds.long_close_grid_qty_pct_0, Bounds.CLOSE_GRID_QTY_PCT_ROUND)), step=Bounds.CLOSE_GRID_QTY_PCT_STEP, format=Bounds.CLOSE_GRID_QTY_PCT_FORMAT, key="edit_opt_v7_long_close_grid_qty_pct_0", help=pbgui_help.close_grid_parameters)
            st.number_input("long_close_trailing_grid_ratio min", min_value=Bounds.CLOSE_TRAILING_GRID_RATIO_MIN, max_value=float(round(self.config.optimize.bounds.long_close_trailing_grid_ratio_1, Bounds.CLOSE_TRAILING_GRID_RATIO_ROUND)), value=float(round(self.config.optimize.bounds.long_close_trailing_grid_ratio_0, Bounds.CLOSE_TRAILING_GRID_RATIO_ROUND)), step=Bounds.CLOSE_TRAILING_GRID_RATIO_STEP, format=Bounds.CLOSE_TRAILING_GRID_RATIO_FORMAT, key="edit_opt_v7_long_close_trailing_grid_ratio_0", help=pbgui_help.trailing_parameters)
            st.number_input("long_close_trailing_qty_pct min", min_value=Bounds.CLOSE_TRAILING_QTY_PCT_MIN, max_value=float(round(self.config.optimize.bounds.long_close_trailing_qty_pct_1, Bounds.CLOSE_TRAILING_QTY_PCT_ROUND)), value=float(round(self.config.optimize.bounds.long_close_trailing_qty_pct_0, Bounds.CLOSE_TRAILING_QTY_PCT_ROUND)), step=Bounds.CLOSE_TRAILING_QTY_PCT_STEP, format=Bounds.CLOSE_TRAILING_QTY_PCT_FORMAT, key="edit_opt_v7_long_close_trailing_qty_pct_0", help=pbgui_help.trailing_parameters)
            st.number_input("long_close_trailing_retracement_pct min", min_value=Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_MIN, max_value=float(round(self.config.optimize.bounds.long_close_trailing_retracement_pct_1, Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_ROUND)), value=float(round(self.config.optimize.bounds.long_close_trailing_retracement_pct_0, Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_ROUND)), step=Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_STEP, format=Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_FORMAT, key="edit_opt_v7_long_close_trailing_retracement_pct_0", help=pbgui_help.trailing_parameters)
            st.number_input("long_close_trailing_threshold_pct min", min_value=Bounds.CLOSE_TRAILING_THRESHOLD_PCT_MIN, max_value=float(round(self.config.optimize.bounds.long_close_trailing_threshold_pct_1, Bounds.CLOSE_TRAILING_THRESHOLD_PCT_ROUND)), value=float(round(self.config.optimize.bounds.long_close_trailing_threshold_pct_0, Bounds.CLOSE_TRAILING_THRESHOLD_PCT_ROUND)), step=Bounds.CLOSE_TRAILING_THRESHOLD_PCT_STEP, format=Bounds.CLOSE_TRAILING_THRESHOLD_PCT_FORMAT, key="edit_opt_v7_long_close_trailing_threshold_pct_0", help=pbgui_help.trailing_parameters)
            st.number_input("long_ema_span_0 min", min_value=Bounds.EMA_SPAN_0_MIN, max_value=float(round(self.config.optimize.bounds.long_ema_span_0_1, Bounds.EMA_SPAN_0_ROUND)), value=float(round(self.config.optimize.bounds.long_ema_span_0_0, Bounds.EMA_SPAN_0_ROUND)), step=Bounds.EMA_SPAN_0_STEP, format=Bounds.EMA_SPAN_0_FORMAT, key="edit_opt_v7_long_ema_span_0_0", help=pbgui_help.ema_span)
            st.number_input("long_ema_span_1 min", min_value=Bounds.EMA_SPAN_1_MIN, max_value=float(round(self.config.optimize.bounds.long_ema_span_1_1, Bounds.EMA_SPAN_1_ROUND)), value=float(round(self.config.optimize.bounds.long_ema_span_1_0, Bounds.EMA_SPAN_1_ROUND)), step=Bounds.EMA_SPAN_1_STEP, format=Bounds.EMA_SPAN_1_FORMAT, key="edit_opt_v7_long_ema_span_1_0", help=pbgui_help.ema_span)
            st.number_input("long_entry_grid_double_down_factor min", min_value=Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_MIN, max_value=float(round(self.config.optimize.bounds.long_entry_grid_double_down_factor_1, Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_ROUND)), value=float(round(self.config.optimize.bounds.long_entry_grid_double_down_factor_0, Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_ROUND)), step=Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_STEP, format=Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_FORMAT, key="edit_opt_v7_long_entry_grid_double_down_factor_0", help=pbgui_help.entry_grid_double_down_factor)
            st.number_input("long_entry_grid_spacing_pct min", min_value=Bounds.ENTRY_GRID_SPACING_PCT_MIN, max_value=float(round(self.config.optimize.bounds.long_entry_grid_spacing_pct_1, Bounds.ENTRY_GRID_SPACING_PCT_ROUND)), value=float(round(self.config.optimize.bounds.long_entry_grid_spacing_pct_0, Bounds.ENTRY_GRID_SPACING_PCT_ROUND)), step=Bounds.ENTRY_GRID_SPACING_PCT_STEP, format=Bounds.ENTRY_GRID_SPACING_PCT_FORMAT, key="edit_opt_v7_long_entry_grid_spacing_pct_0", help=pbgui_help.entry_grid_spacing)
            st.number_input("long_entry_grid_spacing_weight min", min_value=Bounds.ENTRY_GRID_SPACING_WEIGHT_MIN, max_value=float(round(self.config.optimize.bounds.long_entry_grid_spacing_weight_1, Bounds.ENTRY_GRID_SPACING_WEIGHT_ROUND)), value=float(round(self.config.optimize.bounds.long_entry_grid_spacing_weight_0, Bounds.ENTRY_GRID_SPACING_WEIGHT_ROUND)), step=Bounds.ENTRY_GRID_SPACING_WEIGHT_STEP, format=Bounds.ENTRY_GRID_SPACING_WEIGHT_FORMAT, key="edit_opt_v7_long_entry_grid_spacing_weight_0", help=pbgui_help.entry_grid_spacing)
            st.number_input("long_entry_initial_ema_dist min", min_value=Bounds.ENTRY_INITIAL_EMA_DIST_MIN, max_value=float(round(self.config.optimize.bounds.long_entry_initial_ema_dist_1, Bounds.ENTRY_INITIAL_EMA_DIST_ROUND)), value=float(round(self.config.optimize.bounds.long_entry_initial_ema_dist_0, Bounds.ENTRY_INITIAL_EMA_DIST_ROUND)), step=Bounds.ENTRY_INITIAL_EMA_DIST_STEP, format=Bounds.ENTRY_INITIAL_EMA_DIST_FORMAT, key="edit_opt_v7_long_entry_initial_ema_dist_0", help=pbgui_help.entry_initial_ema_dist)
            st.number_input("long_entry_initial_qty_pct min", min_value=Bounds.ENTRY_INITIAL_QTY_PCT_MIN, max_value=float(round(self.config.optimize.bounds.long_entry_initial_qty_pct_1, Bounds.ENTRY_INITIAL_QTY_PCT_ROUND)), value=float(round(self.config.optimize.bounds.long_entry_initial_qty_pct_0, Bounds.ENTRY_INITIAL_QTY_PCT_ROUND)), step=Bounds.ENTRY_INITIAL_QTY_PCT_STEP, format=Bounds.ENTRY_INITIAL_QTY_PCT_FORMAT, key="edit_opt_v7_long_entry_initial_qty_pct_0", help=pbgui_help.entry_initial_qty_pct)
            st.number_input("long_entry_trailing_grid_ratio min", min_value=Bounds.ENTRY_TRAILING_GRID_RATIO_MIN, max_value=float(round(self.config.optimize.bounds.long_entry_trailing_grid_ratio_1, Bounds.ENTRY_TRAILING_GRID_RATIO_ROUND)), value=float(round(self.config.optimize.bounds.long_entry_trailing_grid_ratio_0, Bounds.ENTRY_TRAILING_GRID_RATIO_ROUND)), step=Bounds.ENTRY_TRAILING_GRID_RATIO_STEP, format=Bounds.ENTRY_TRAILING_GRID_RATIO_FORMAT, key="edit_opt_v7_long_entry_trailing_grid_ratio_0", help=pbgui_help.trailing_parameters)
            st.number_input("long_entry_trailing_retracement_pct min", min_value=Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_MIN, max_value=float(round(self.config.optimize.bounds.long_entry_trailing_retracement_pct_1, Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_ROUND)), value=float(round(self.config.optimize.bounds.long_entry_trailing_retracement_pct_0, Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_ROUND)), step=Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_STEP, format=Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_FORMAT, key="edit_opt_v7_long_entry_trailing_retracement_pct_0", help=pbgui_help.trailing_parameters)
            st.number_input("long_entry_trailing_threshold_pct min", min_value=Bounds.ENTRY_TRAILING_THRESHOLD_PCT_MIN, max_value=float(round(self.config.optimize.bounds.long_entry_trailing_threshold_pct_1, Bounds.ENTRY_TRAILING_THRESHOLD_PCT_ROUND)), value=float(round(self.config.optimize.bounds.long_entry_trailing_threshold_pct_0, Bounds.ENTRY_TRAILING_THRESHOLD_PCT_ROUND)), step=Bounds.ENTRY_TRAILING_THRESHOLD_PCT_STEP, format=Bounds.ENTRY_TRAILING_THRESHOLD_PCT_FORMAT, key="edit_opt_v7_long_entry_trailing_threshold_pct_0", help=pbgui_help.trailing_parameters)
            st.number_input("long_filter_relative_volume_clip_pct min", min_value=Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_MIN, max_value=float(round(self.config.optimize.bounds.long_filter_relative_volume_clip_pct_1, Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_ROUND)), value=float(round(self.config.optimize.bounds.long_filter_relative_volume_clip_pct_0, Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_ROUND)), step=Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_STEP, format=Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_FORMAT, key="edit_opt_v7_long_filter_relative_volume_clip_pct_0", help=pbgui_help.filter_relative_volume_clip_pct)
            st.number_input("long_filter_rolling_window min", min_value=Bounds.FILTER_ROLLING_WINDOW_MIN, max_value=float(round(self.config.optimize.bounds.long_filter_rolling_window_1, Bounds.FILTER_ROLLING_WINDOW_ROUND)), value=float(round(self.config.optimize.bounds.long_filter_rolling_window_0, Bounds.FILTER_ROLLING_WINDOW_ROUND)), step=Bounds.FILTER_ROLLING_WINDOW_STEP, format=Bounds.FILTER_ROLLING_WINDOW_FORMAT, key="edit_opt_v7_long_filter_rolling_window_0", help=pbgui_help.filter_rolling_window)
            st.number_input("long_n_positions min", min_value=Bounds.N_POSITIONS_MIN, max_value=float(round(self.config.optimize.bounds.long_n_positions_1, Bounds.N_POSITIONS_ROUND)), value=float(round(self.config.optimize.bounds.long_n_positions_0, Bounds.N_POSITIONS_ROUND)), step=Bounds.N_POSITIONS_STEP, format=Bounds.N_POSITIONS_FORMAT, key="edit_opt_v7_long_n_positions_0", help=pbgui_help.n_positions)
            st.number_input("long_total_wallet_exposure_limit min", min_value=Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_MIN, max_value=float(round(self.config.optimize.bounds.long_total_wallet_exposure_limit_1, Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_ROUND)), value=float(round(self.config.optimize.bounds.long_total_wallet_exposure_limit_0, Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_ROUND)), step=Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_STEP, format=Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_FORMAT, key="edit_opt_v7_long_total_wallet_exposure_limit_0", help=pbgui_help.total_wallet_exposure_limit)
            st.number_input("long_unstuck_close_pct min", min_value=Bounds.UNSTUCK_CLOSE_PCT_MIN, max_value=float(round(self.config.optimize.bounds.long_unstuck_close_pct_1, Bounds.UNSTUCK_CLOSE_PCT_ROUND)), value=float(round(self.config.optimize.bounds.long_unstuck_close_pct_0, Bounds.UNSTUCK_CLOSE_PCT_ROUND)), step=Bounds.UNSTUCK_CLOSE_PCT_STEP, format=Bounds.UNSTUCK_CLOSE_PCT_FORMAT, key="edit_opt_v7_long_unstuck_close_pct_0", help=pbgui_help.unstuck_close_pct)
            st.number_input("long_unstuck_ema_dist min", min_value=Bounds.UNSTUCK_EMA_DIST_MIN, max_value=float(round(self.config.optimize.bounds.long_unstuck_ema_dist_1, Bounds.UNSTUCK_EMA_DIST_ROUND)), value=float(round(self.config.optimize.bounds.long_unstuck_ema_dist_0, Bounds.UNSTUCK_EMA_DIST_ROUND)), step=Bounds.UNSTUCK_EMA_DIST_STEP, format=Bounds.UNSTUCK_EMA_DIST_FORMAT, key="edit_opt_v7_long_unstuck_ema_dist_0", help=pbgui_help.unstuck_ema_dist)
            st.number_input("long_unstuck_loss_allowance_pct min", min_value=Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_MIN, max_value=float(round(self.config.optimize.bounds.long_unstuck_loss_allowance_pct_1, Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_ROUND)), value=float(round(self.config.optimize.bounds.long_unstuck_loss_allowance_pct_0, Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_ROUND)), step=Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_STEP, format=Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_FORMAT, key="edit_opt_v7_long_unstuck_loss_allowance_pct_0", help=pbgui_help.unstuck_loss_allowance_pct)
            st.number_input("long_unstuck_threshold min", min_value=Bounds.UNSTUCK_THRESHOLD_MIN, max_value=float(round(self.config.optimize.bounds.long_unstuck_threshold_1, Bounds.UNSTUCK_THRESHOLD_ROUND)), value=float(round(self.config.optimize.bounds.long_unstuck_threshold_0, Bounds.UNSTUCK_THRESHOLD_ROUND)), step=Bounds.UNSTUCK_THRESHOLD_STEP, format=Bounds.UNSTUCK_THRESHOLD_FORMAT, key="edit_opt_v7_long_unstuck_threshold_0", help=pbgui_help.unstuck_threshold)
        with col2:
            st.number_input("long_close_grid_markup_range max", min_value=float(round(self.config.optimize.bounds.long_close_grid_markup_range_0, Bounds.CLOSE_GRID_MARKUP_RANGE_ROUND)), max_value=Bounds.CLOSE_GRID_MARKUP_RANGE_MAX, value=float(round(self.config.optimize.bounds.long_close_grid_markup_range_1, Bounds.CLOSE_GRID_MARKUP_RANGE_ROUND)), step=Bounds.CLOSE_GRID_MARKUP_RANGE_STEP, format=Bounds.CLOSE_GRID_MARKUP_RANGE_FORMAT, key="edit_opt_v7_long_close_grid_markup_range_1", help=pbgui_help.close_grid_parameters)
            st.number_input("long_close_grid_min_markup max", min_value=float(round(self.config.optimize.bounds.long_close_grid_min_markup_0, Bounds.CLOSE_GRID_MIN_MARKUP_ROUND)), max_value=Bounds.CLOSE_GRID_MIN_MARKUP_MAX, value=float(round(self.config.optimize.bounds.long_close_grid_min_markup_1, Bounds.CLOSE_GRID_MIN_MARKUP_ROUND)), step=Bounds.CLOSE_GRID_MIN_MARKUP_STEP, format=Bounds.CLOSE_GRID_MIN_MARKUP_FORMAT, key="edit_opt_v7_long_close_grid_min_markup_1", help=pbgui_help.close_grid_parameters)
            st.number_input("long_close_grid_qty_pct max", min_value=float(round(self.config.optimize.bounds.long_close_grid_qty_pct_0, Bounds.CLOSE_GRID_QTY_PCT_ROUND)), max_value=Bounds.CLOSE_GRID_QTY_PCT_MAX, value=float(round(self.config.optimize.bounds.long_close_grid_qty_pct_1, Bounds.CLOSE_GRID_QTY_PCT_ROUND)), step=Bounds.CLOSE_GRID_QTY_PCT_STEP, format=Bounds.CLOSE_GRID_QTY_PCT_FORMAT, key="edit_opt_v7_long_close_grid_qty_pct_1", help=pbgui_help.close_grid_parameters)
            st.number_input("long_close_trailing_grid_ratio max", min_value=float(round(self.config.optimize.bounds.long_close_trailing_grid_ratio_0, Bounds.CLOSE_TRAILING_GRID_RATIO_ROUND)), max_value=Bounds.CLOSE_TRAILING_GRID_RATIO_MAX, value=float(round(self.config.optimize.bounds.long_close_trailing_grid_ratio_1, Bounds.CLOSE_TRAILING_GRID_RATIO_ROUND)), step=Bounds.CLOSE_TRAILING_GRID_RATIO_STEP, format=Bounds.CLOSE_TRAILING_GRID_RATIO_FORMAT, key="edit_opt_v7_long_close_trailing_grid_ratio_1", help=pbgui_help.trailing_parameters)
            st.number_input("long_close_trailing_qty_pct max", min_value=float(round(self.config.optimize.bounds.long_close_trailing_qty_pct_0, Bounds.CLOSE_TRAILING_QTY_PCT_ROUND)), max_value=Bounds.CLOSE_TRAILING_QTY_PCT_MAX, value=float(round(self.config.optimize.bounds.long_close_trailing_qty_pct_1, Bounds.CLOSE_TRAILING_QTY_PCT_ROUND)), step=Bounds.CLOSE_TRAILING_QTY_PCT_STEP, format=Bounds.CLOSE_TRAILING_QTY_PCT_FORMAT, key="edit_opt_v7_long_close_trailing_qty_pct_1", help=pbgui_help.trailing_parameters)
            st.number_input("long_close_trailing_retracement_pct max", min_value=float(round(self.config.optimize.bounds.long_close_trailing_retracement_pct_0, Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_ROUND)), max_value=Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_MAX, value=float(round(self.config.optimize.bounds.long_close_trailing_retracement_pct_1, Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_ROUND)), step=Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_STEP, format=Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_FORMAT, key="edit_opt_v7_long_close_trailing_retracement_pct_1", help=pbgui_help.trailing_parameters)
            st.number_input("long_close_trailing_threshold_pct max", min_value=float(round(self.config.optimize.bounds.long_close_trailing_threshold_pct_0, Bounds.CLOSE_TRAILING_THRESHOLD_PCT_ROUND)), max_value=Bounds.CLOSE_TRAILING_THRESHOLD_PCT_MAX, value=float(round(self.config.optimize.bounds.long_close_trailing_threshold_pct_1, Bounds.CLOSE_TRAILING_THRESHOLD_PCT_ROUND)), step=Bounds.CLOSE_TRAILING_THRESHOLD_PCT_STEP, format=Bounds.CLOSE_TRAILING_THRESHOLD_PCT_FORMAT, key="edit_opt_v7_long_close_trailing_threshold_pct_1", help=pbgui_help.trailing_parameters)
            st.number_input("long_ema_span_0 max", min_value=float(round(self.config.optimize.bounds.long_ema_span_0_0, Bounds.EMA_SPAN_0_ROUND)), max_value=Bounds.EMA_SPAN_0_MAX, value=float(round(self.config.optimize.bounds.long_ema_span_0_1, Bounds.EMA_SPAN_0_ROUND)), step=Bounds.EMA_SPAN_0_STEP, format=Bounds.EMA_SPAN_0_FORMAT, key="edit_opt_v7_long_ema_span_0_1", help=pbgui_help.ema_span)
            st.number_input("long_ema_span_1 max", min_value=float(round(self.config.optimize.bounds.long_ema_span_1_0, Bounds.EMA_SPAN_1_ROUND)), max_value=Bounds.EMA_SPAN_1_MAX, value=float(round(self.config.optimize.bounds.long_ema_span_1_1, Bounds.EMA_SPAN_1_ROUND)), step=Bounds.EMA_SPAN_1_STEP, format=Bounds.EMA_SPAN_1_FORMAT, key="edit_opt_v7_long_ema_span_1_1", help=pbgui_help.ema_span)
            st.number_input("long_entry_grid_double_down_factor max", min_value=float(round(self.config.optimize.bounds.long_entry_grid_double_down_factor_0, Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_ROUND)), max_value=Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_MAX, value=float(round(self.config.optimize.bounds.long_entry_grid_double_down_factor_1, Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_ROUND)), step=Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_STEP, format=Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_FORMAT, key="edit_opt_v7_long_entry_grid_double_down_factor_1", help=pbgui_help.entry_grid_double_down_factor)
            st.number_input("long_entry_grid_spacing_pct max", min_value=float(round(self.config.optimize.bounds.long_entry_grid_spacing_pct_0, Bounds.ENTRY_GRID_SPACING_PCT_ROUND)), max_value=Bounds.ENTRY_GRID_SPACING_PCT_MAX, value=float(round(self.config.optimize.bounds.long_entry_grid_spacing_pct_1, Bounds.ENTRY_GRID_SPACING_PCT_ROUND)), step=Bounds.ENTRY_GRID_SPACING_PCT_STEP, format=Bounds.ENTRY_GRID_SPACING_PCT_FORMAT, key="edit_opt_v7_long_entry_grid_spacing_pct_1", help=pbgui_help.entry_grid_spacing)
            st.number_input("long_entry_grid_spacing_weight max", min_value=float(round(self.config.optimize.bounds.long_entry_grid_spacing_weight_0, Bounds.ENTRY_GRID_SPACING_WEIGHT_ROUND)), max_value=Bounds.ENTRY_GRID_SPACING_WEIGHT_MAX, value=float(round(self.config.optimize.bounds.long_entry_grid_spacing_weight_1, Bounds.ENTRY_GRID_SPACING_WEIGHT_ROUND)), step=Bounds.ENTRY_GRID_SPACING_WEIGHT_STEP, format=Bounds.ENTRY_GRID_SPACING_WEIGHT_FORMAT, key="edit_opt_v7_long_entry_grid_spacing_weight_1", help=pbgui_help.entry_grid_spacing)
            st.number_input("long_entry_initial_ema_dist max", min_value=float(round(self.config.optimize.bounds.long_entry_initial_ema_dist_0, Bounds.ENTRY_INITIAL_EMA_DIST_ROUND)), max_value=Bounds.ENTRY_INITIAL_EMA_DIST_MAX, value=float(round(self.config.optimize.bounds.long_entry_initial_ema_dist_1, Bounds.ENTRY_INITIAL_EMA_DIST_ROUND)), step=Bounds.ENTRY_INITIAL_EMA_DIST_STEP, format=Bounds.ENTRY_INITIAL_EMA_DIST_FORMAT, key="edit_opt_v7_long_entry_initial_ema_dist_1", help=pbgui_help.entry_initial_ema_dist)
            st.number_input("long_entry_initial_qty_pct max", min_value=float(round(self.config.optimize.bounds.long_entry_initial_qty_pct_0, Bounds.ENTRY_INITIAL_QTY_PCT_ROUND)), max_value=Bounds.ENTRY_INITIAL_QTY_PCT_MAX, value=float(round(self.config.optimize.bounds.long_entry_initial_qty_pct_1, Bounds.ENTRY_INITIAL_QTY_PCT_ROUND)), step=Bounds.ENTRY_INITIAL_QTY_PCT_STEP, format=Bounds.ENTRY_INITIAL_QTY_PCT_FORMAT, key="edit_opt_v7_long_entry_initial_qty_pct_1", help=pbgui_help.entry_initial_qty_pct)
            st.number_input("long_entry_trailing_grid_ratio max", min_value=float(round(self.config.optimize.bounds.long_entry_trailing_grid_ratio_0, Bounds.ENTRY_TRAILING_GRID_RATIO_ROUND)), max_value=Bounds.ENTRY_TRAILING_GRID_RATIO_MAX, value=float(round(self.config.optimize.bounds.long_entry_trailing_grid_ratio_1, Bounds.ENTRY_TRAILING_GRID_RATIO_ROUND)), step=Bounds.ENTRY_TRAILING_GRID_RATIO_STEP, format=Bounds.ENTRY_TRAILING_GRID_RATIO_FORMAT, key="edit_opt_v7_long_entry_trailing_grid_ratio_1", help=pbgui_help.trailing_parameters)
            st.number_input("long_entry_trailing_retracement_pct max", min_value=float(round(self.config.optimize.bounds.long_entry_trailing_retracement_pct_0, Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_ROUND)), max_value=Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_MAX, value=float(round(self.config.optimize.bounds.long_entry_trailing_retracement_pct_1, Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_ROUND)), step=Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_STEP, format=Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_FORMAT, key="edit_opt_v7_long_entry_trailing_retracement_pct_1", help=pbgui_help.trailing_parameters)
            st.number_input("long_entry_trailing_threshold_pct max", min_value=float(round(self.config.optimize.bounds.long_entry_trailing_threshold_pct_0, Bounds.ENTRY_TRAILING_THRESHOLD_PCT_ROUND)), max_value=Bounds.ENTRY_TRAILING_THRESHOLD_PCT_MAX, value=float(round(self.config.optimize.bounds.long_entry_trailing_threshold_pct_1, Bounds.ENTRY_TRAILING_THRESHOLD_PCT_ROUND)), step=Bounds.ENTRY_TRAILING_THRESHOLD_PCT_STEP, format=Bounds.ENTRY_TRAILING_THRESHOLD_PCT_FORMAT, key="edit_opt_v7_long_entry_trailing_threshold_pct_1", help=pbgui_help.trailing_parameters)
            st.number_input("long_filter_relative_volume_clip_pct max", min_value=float(round(self.config.optimize.bounds.long_filter_relative_volume_clip_pct_0, Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_ROUND)), max_value=Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_MAX, value=float(round(self.config.optimize.bounds.long_filter_relative_volume_clip_pct_1, Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_ROUND)), step=Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_STEP, format=Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_FORMAT, key="edit_opt_v7_long_filter_relative_volume_clip_pct_1", help=pbgui_help.filter_relative_volume_clip_pct)
            st.number_input("long_filter_rolling_window max", min_value=float(round(self.config.optimize.bounds.long_filter_rolling_window_0, Bounds.FILTER_ROLLING_WINDOW_ROUND)), max_value=Bounds.FILTER_ROLLING_WINDOW_MAX, value=float(round(self.config.optimize.bounds.long_filter_rolling_window_1, Bounds.FILTER_ROLLING_WINDOW_ROUND)), step=Bounds.FILTER_ROLLING_WINDOW_STEP, format=Bounds.FILTER_ROLLING_WINDOW_FORMAT, key="edit_opt_v7_long_filter_rolling_window_1", help=pbgui_help.filter_rolling_window)
            st.number_input("long_n_positions max", min_value=float(round(self.config.optimize.bounds.long_n_positions_0, Bounds.N_POSITIONS_ROUND)), max_value=Bounds.N_POSITIONS_MAX, value=float(round(self.config.optimize.bounds.long_n_positions_1, Bounds.N_POSITIONS_ROUND)), step=Bounds.N_POSITIONS_STEP, format=Bounds.N_POSITIONS_FORMAT, key="edit_opt_v7_long_n_positions_1", help=pbgui_help.n_positions)
            st.number_input("long_total_wallet_exposure_limit max", min_value=float(round(self.config.optimize.bounds.long_total_wallet_exposure_limit_0, Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_ROUND)), max_value=Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_MAX, value=float(round(self.config.optimize.bounds.long_total_wallet_exposure_limit_1, Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_ROUND)), step=Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_STEP, format=Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_FORMAT, key="edit_opt_v7_long_total_wallet_exposure_limit_1", help=pbgui_help.total_wallet_exposure_limit)
            st.number_input("long_unstuck_close_pct max", min_value=float(round(self.config.optimize.bounds.long_unstuck_close_pct_0, Bounds.UNSTUCK_CLOSE_PCT_ROUND)), max_value=Bounds.UNSTUCK_CLOSE_PCT_MAX, value=float(round(self.config.optimize.bounds.long_unstuck_close_pct_1, Bounds.UNSTUCK_CLOSE_PCT_ROUND)), step=Bounds.UNSTUCK_CLOSE_PCT_STEP, format=Bounds.UNSTUCK_CLOSE_PCT_FORMAT, key="edit_opt_v7_long_unstuck_close_pct_1", help=pbgui_help.unstuck_close_pct)
            st.number_input("long_unstuck_ema_dist max", min_value=float(round(self.config.optimize.bounds.long_unstuck_ema_dist_0, Bounds.UNSTUCK_EMA_DIST_ROUND)), max_value=Bounds.UNSTUCK_EMA_DIST_MAX, value=float(round(self.config.optimize.bounds.long_unstuck_ema_dist_1, Bounds.UNSTUCK_EMA_DIST_ROUND)), step=Bounds.UNSTUCK_EMA_DIST_STEP, format=Bounds.UNSTUCK_EMA_DIST_FORMAT, key="edit_opt_v7_long_unstuck_ema_dist_1", help=pbgui_help.unstuck_ema_dist)
            st.number_input("long_unstuck_loss_allowance_pct max", min_value=float(round(self.config.optimize.bounds.long_unstuck_loss_allowance_pct_0, Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_ROUND)), max_value=Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_MAX, value=float(round(self.config.optimize.bounds.long_unstuck_loss_allowance_pct_1, Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_ROUND)), step=Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_STEP, format=Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_FORMAT, key="edit_opt_v7_long_unstuck_loss_allowance_pct_1", help=pbgui_help.unstuck_loss_allowance_pct)
            st.number_input("long_unstuck_threshold max", min_value=float(round(self.config.optimize.bounds.long_unstuck_threshold_0, Bounds.UNSTUCK_THRESHOLD_ROUND)), max_value=Bounds.UNSTUCK_THRESHOLD_MAX, value=float(round(self.config.optimize.bounds.long_unstuck_threshold_1, Bounds.UNSTUCK_THRESHOLD_ROUND)), step=Bounds.UNSTUCK_THRESHOLD_STEP, format=Bounds.UNSTUCK_THRESHOLD_FORMAT, key="edit_opt_v7_long_unstuck_threshold_1", help=pbgui_help.unstuck_threshold)
        with col3:
            # short_close_grid_markup_range
            if "edit_opt_v7_short_close_grid_markup_range_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_close_grid_markup_range_0 != self.config.optimize.bounds.short_close_grid_markup_range_0:
                    self.config.optimize.bounds.short_close_grid_markup_range_0 = st.session_state.edit_opt_v7_short_close_grid_markup_range_0
            if "edit_opt_v7_short_close_grid_markup_range_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_close_grid_markup_range_1 != self.config.optimize.bounds.short_close_grid_markup_range_1:
                    self.config.optimize.bounds.short_close_grid_markup_range_1 = st.session_state.edit_opt_v7_short_close_grid_markup_range_1
            # short_close_grid_min_markup
            if "edit_opt_v7_short_close_grid_min_markup_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_close_grid_min_markup_0 != self.config.optimize.bounds.short_close_grid_min_markup_0:
                    self.config.optimize.bounds.short_close_grid_min_markup_0 = st.session_state.edit_opt_v7_short_close_grid_min_markup_0
            if "edit_opt_v7_short_close_grid_min_markup_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_close_grid_min_markup_1 != self.config.optimize.bounds.short_close_grid_min_markup_1:
                    self.config.optimize.bounds.short_close_grid_min_markup_1 = st.session_state.edit_opt_v7_short_close_grid_min_markup_1
            # short_close_grid_qty_pct
            if "edit_opt_v7_short_close_grid_qty_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_close_grid_qty_pct_0 != self.config.optimize.bounds.short_close_grid_qty_pct_0:
                    self.config.optimize.bounds.short_close_grid_qty_pct_0 = st.session_state.edit_opt_v7_short_close_grid_qty_pct_0
            if "edit_opt_v7_short_close_grid_qty_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_close_grid_qty_pct_1 != self.config.optimize.bounds.short_close_grid_qty_pct_1:
                    self.config.optimize.bounds.short_close_grid_qty_pct_1 = st.session_state.edit_opt_v7_short_close_grid_qty_pct_1
            # short_close_trailing_grid_ratio
            if "edit_opt_v7_short_close_trailing_grid_ratio_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_close_trailing_grid_ratio_0 != self.config.optimize.bounds.short_close_trailing_grid_ratio_0:
                    self.config.optimize.bounds.short_close_trailing_grid_ratio_0 = st.session_state.edit_opt_v7_short_close_trailing_grid_ratio_0
            if "edit_opt_v7_short_close_trailing_grid_ratio_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_close_trailing_grid_ratio_1 != self.config.optimize.bounds.short_close_trailing_grid_ratio_1:
                    self.config.optimize.bounds.short_close_trailing_grid_ratio_1 = st.session_state.edit_opt_v7_short_close_trailing_grid_ratio_1
            # short_close_trailing_qty_pct
            if "edit_opt_v7_short_close_trailing_qty_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_close_trailing_qty_pct_0 != self.config.optimize.bounds.short_close_trailing_qty_pct_0:
                    self.config.optimize.bounds.short_close_trailing_qty_pct_0 = st.session_state.edit_opt_v7_short_close_trailing_qty_pct_0
            if "edit_opt_v7_short_close_trailing_qty_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_close_trailing_qty_pct_1 != self.config.optimize.bounds.short_close_trailing_qty_pct_1:
                    self.config.optimize.bounds.short_close_trailing_qty_pct_1 = st.session_state.edit_opt_v7_short_close_trailing_qty_pct_1
            # short_close_trailing_retracement_pct
            if "edit_opt_v7_short_close_trailing_retracement_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_close_trailing_retracement_pct_0 != self.config.optimize.bounds.short_close_trailing_retracement_pct_0:
                    self.config.optimize.bounds.short_close_trailing_retracement_pct_0 = st.session_state.edit_opt_v7_short_close_trailing_retracement_pct_0
            if "edit_opt_v7_short_close_trailing_retracement_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_close_trailing_retracement_pct_1 != self.config.optimize.bounds.short_close_trailing_retracement_pct_1:
                    self.config.optimize.bounds.short_close_trailing_retracement_pct_1 = st.session_state.edit_opt_v7_short_close_trailing_retracement_pct_1
            # short_close_trailing_threshold_pct
            if "edit_opt_v7_short_close_trailing_threshold_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_close_trailing_threshold_pct_0 != self.config.optimize.bounds.short_close_trailing_threshold_pct_0:
                    self.config.optimize.bounds.short_close_trailing_threshold_pct_0 = st.session_state.edit_opt_v7_short_close_trailing_threshold_pct_0
            if "edit_opt_v7_short_close_trailing_threshold_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_close_trailing_threshold_pct_1 != self.config.optimize.bounds.short_close_trailing_threshold_pct_1:
                    self.config.optimize.bounds.short_close_trailing_threshold_pct_1 = st.session_state.edit_opt_v7_short_close_trailing_threshold_pct_1
            # short_ema_span_0
            if "edit_opt_v7_short_ema_span_0_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_ema_span_0_0 != self.config.optimize.bounds.short_ema_span_0_0:
                    self.config.optimize.bounds.short_ema_span_0_0 = st.session_state.edit_opt_v7_short_ema_span_0_0
            if "edit_opt_v7_short_ema_span_0_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_ema_span_0_1 != self.config.optimize.bounds.short_ema_span_0_1:
                    self.config.optimize.bounds.short_ema_span_0_1 = st.session_state.edit_opt_v7_short_ema_span_0_1
            # short_ema_span_1
            if "edit_opt_v7_short_ema_span_1_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_ema_span_1_0 != self.config.optimize.bounds.short_ema_span_1_0:
                    self.config.optimize.bounds.short_ema_span_1_0 = st.session_state.edit_opt_v7_short_ema_span_1_0
            if "edit_opt_v7_short_ema_span_1_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_ema_span_1_1 != self.config.optimize.bounds.short_ema_span_1_1:
                    self.config.optimize.bounds.short_ema_span_1_1 = st.session_state.edit_opt_v7_short_ema_span_1_1
            # short_entry_grid_double_down_factor
            if "edit_opt_v7_short_entry_grid_double_down_factor_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_entry_grid_double_down_factor_0 != self.config.optimize.bounds.short_entry_grid_double_down_factor_0:
                    self.config.optimize.bounds.short_entry_grid_double_down_factor_0 = st.session_state.edit_opt_v7_short_entry_grid_double_down_factor_0
            if "edit_opt_v7_short_entry_grid_double_down_factor_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_entry_grid_double_down_factor_1 != self.config.optimize.bounds.short_entry_grid_double_down_factor_1:
                    self.config.optimize.bounds.short_entry_grid_double_down_factor_1 = st.session_state.edit_opt_v7_short_entry_grid_double_down_factor_1
            # short_entry_grid_spacing_pct
            if "edit_opt_v7_short_entry_grid_spacing_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_entry_grid_spacing_pct_0 != self.config.optimize.bounds.short_entry_grid_spacing_pct_0:
                    self.config.optimize.bounds.short_entry_grid_spacing_pct_0 = st.session_state.edit_opt_v7_short_entry_grid_spacing_pct_0
            if "edit_opt_v7_short_entry_grid_spacing_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_entry_grid_spacing_pct_1 != self.config.optimize.bounds.short_entry_grid_spacing_pct_1:
                    self.config.optimize.bounds.short_entry_grid_spacing_pct_1 = st.session_state.edit_opt_v7_short_entry_grid_spacing_pct_1
            # short_entry_grid_spacing_weight
            if "edit_opt_v7_short_entry_grid_spacing_weight_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_entry_grid_spacing_weight_0 != self.config.optimize.bounds.short_entry_grid_spacing_weight_0:
                    self.config.optimize.bounds.short_entry_grid_spacing_weight_0 = st.session_state.edit_opt_v7_short_entry_grid_spacing_weight_0
            if "edit_opt_v7_short_entry_grid_spacing_weight_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_entry_grid_spacing_weight_1 != self.config.optimize.bounds.short_entry_grid_spacing_weight_1:
                    self.config.optimize.bounds.short_entry_grid_spacing_weight_1 = st.session_state.edit_opt_v7_short_entry_grid_spacing_weight_1
            # short_entry_initial_ema_dist
            if "edit_opt_v7_short_entry_initial_ema_dist_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_entry_initial_ema_dist_0 != self.config.optimize.bounds.short_entry_initial_ema_dist_0:
                    self.config.optimize.bounds.short_entry_initial_ema_dist_0 = st.session_state.edit_opt_v7_short_entry_initial_ema_dist_0
            if "edit_opt_v7_short_entry_initial_ema_dist_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_entry_initial_ema_dist_1 != self.config.optimize.bounds.short_entry_initial_ema_dist_1:
                    self.config.optimize.bounds.short_entry_initial_ema_dist_1 = st.session_state.edit_opt_v7_short_entry_initial_ema_dist_1
            # short_entry_initial_qty_pct
            if "edit_opt_v7_short_entry_initial_qty_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_entry_initial_qty_pct_0 != self.config.optimize.bounds.short_entry_initial_qty_pct_0:
                    self.config.optimize.bounds.short_entry_initial_qty_pct_0 = st.session_state.edit_opt_v7_short_entry_initial_qty_pct_0
            if "edit_opt_v7_short_entry_initial_qty_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_entry_initial_qty_pct_1 != self.config.optimize.bounds.short_entry_initial_qty_pct_1:
                    self.config.optimize.bounds.short_entry_initial_qty_pct_1 = st.session_state.edit_opt_v7_short_entry_initial_qty_pct_1
            # short_entry_trailing_grid_ratio
            if "edit_opt_v7_short_entry_trailing_grid_ratio_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_entry_trailing_grid_ratio_0 != self.config.optimize.bounds.short_entry_trailing_grid_ratio_0:
                    self.config.optimize.bounds.short_entry_trailing_grid_ratio_0 = st.session_state.edit_opt_v7_short_entry_trailing_grid_ratio_0
            if "edit_opt_v7_short_entry_trailing_grid_ratio_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_entry_trailing_grid_ratio_1 != self.config.optimize.bounds.short_entry_trailing_grid_ratio_1:
                    self.config.optimize.bounds.short_entry_trailing_grid_ratio_1 = st.session_state.edit_opt_v7_short_entry_trailing_grid_ratio_1
            # short_entry_trailing_retracement_pct
            if "edit_opt_v7_short_entry_trailing_retracement_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_entry_trailing_retracement_pct_0 != self.config.optimize.bounds.short_entry_trailing_retracement_pct_0:
                    self.config.optimize.bounds.short_entry_trailing_retracement_pct_0 = st.session_state.edit_opt_v7_short_entry_trailing_retracement_pct_0
            if "edit_opt_v7_short_entry_trailing_retracement_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_entry_trailing_retracement_pct_1 != self.config.optimize.bounds.short_entry_trailing_retracement_pct_1:
                    self.config.optimize.bounds.short_entry_trailing_retracement_pct_1 = st.session_state.edit_opt_v7_short_entry_trailing_retracement_pct_1
            # short_entry_trailing_threshold_pct
            if "edit_opt_v7_short_entry_trailing_threshold_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_entry_trailing_threshold_pct_0 != self.config.optimize.bounds.short_entry_trailing_threshold_pct_0:
                    self.config.optimize.bounds.short_entry_trailing_threshold_pct_0 = st.session_state.edit_opt_v7_short_entry_trailing_threshold_pct_0
            if "edit_opt_v7_short_entry_trailing_threshold_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_entry_trailing_threshold_pct_1 != self.config.optimize.bounds.short_entry_trailing_threshold_pct_1:
                    self.config.optimize.bounds.short_entry_trailing_threshold_pct_1 = st.session_state.edit_opt_v7_short_entry_trailing_threshold_pct_1
            # short_filter_relative_volume_clip_pct
            if "edit_opt_v7_short_filter_relative_volume_clip_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_filter_relative_volume_clip_pct_0 != self.config.optimize.bounds.short_filter_relative_volume_clip_pct_0:
                    self.config.optimize.bounds.short_filter_relative_volume_clip_pct_0 = st.session_state.edit_opt_v7_short_filter_relative_volume_clip_pct_0
            if "edit_opt_v7_short_filter_relative_volume_clip_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_filter_relative_volume_clip_pct_1 != self.config.optimize.bounds.short_filter_relative_volume_clip_pct_1:
                    self.config.optimize.bounds.short_filter_relative_volume_clip_pct_1 = st.session_state.edit_opt_v7_short_filter_relative_volume_clip_pct_1
            # short_filter_rolling_window
            if "edit_opt_v7_short_filter_rolling_window_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_filter_rolling_window_0 != self.config.optimize.bounds.short_filter_rolling_window_0:
                    self.config.optimize.bounds.short_filter_rolling_window_0 = st.session_state.edit_opt_v7_short_filter_rolling_window_0
            if "edit_opt_v7_short_filter_rolling_window_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_filter_rolling_window_1 != self.config.optimize.bounds.short_filter_rolling_window_1:
                    self.config.optimize.bounds.short_filter_rolling_window_1 = st.session_state.edit_opt_v7_short_filter_rolling_window_1
            # short_n_positions
            if "edit_opt_v7_short_n_positions_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_n_positions_0 != self.config.optimize.bounds.short_n_positions_0:
                    self.config.optimize.bounds.short_n_positions_0 = st.session_state.edit_opt_v7_short_n_positions_0
            if "edit_opt_v7_short_n_positions_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_n_positions_1 != self.config.optimize.bounds.short_n_positions_1:
                    self.config.optimize.bounds.short_n_positions_1 = st.session_state.edit_opt_v7_short_n_positions_1
            # short_total_wallet_exposure_limit
            if "edit_opt_v7_short_total_wallet_exposure_limit_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_total_wallet_exposure_limit_0 != self.config.optimize.bounds.short_total_wallet_exposure_limit_0:
                    self.config.optimize.bounds.short_total_wallet_exposure_limit_0 = st.session_state.edit_opt_v7_short_total_wallet_exposure_limit_0
            if "edit_opt_v7_short_total_wallet_exposure_limit_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_total_wallet_exposure_limit_1 != self.config.optimize.bounds.short_total_wallet_exposure_limit_1:
                    self.config.optimize.bounds.short_total_wallet_exposure_limit_1 = st.session_state.edit_opt_v7_short_total_wallet_exposure_limit_1
            # short_unstuck_close_pct
            if "edit_opt_v7_short_unstuck_close_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_unstuck_close_pct_0 != self.config.optimize.bounds.short_unstuck_close_pct_0:
                    self.config.optimize.bounds.short_unstuck_close_pct_0 = st.session_state.edit_opt_v7_short_unstuck_close_pct_0
            if "edit_opt_v7_short_unstuck_close_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_unstuck_close_pct_1 != self.config.optimize.bounds.short_unstuck_close_pct_1:
                    self.config.optimize.bounds.short_unstuck_close_pct_1 = st.session_state.edit_opt_v7_short_unstuck_close_pct_1
            # short_unstuck_ema_dist
            if "edit_opt_v7_short_unstuck_ema_dist_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_unstuck_ema_dist_0 != self.config.optimize.bounds.short_unstuck_ema_dist_0:
                    self.config.optimize.bounds.short_unstuck_ema_dist_0 = st.session_state.edit_opt_v7_short_unstuck_ema_dist_0
            if "edit_opt_v7_short_unstuck_ema_dist_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_unstuck_ema_dist_1 != self.config.optimize.bounds.short_unstuck_ema_dist_1:
                    self.config.optimize.bounds.short_unstuck_ema_dist_1 = st.session_state.edit_opt_v7_short_unstuck_ema_dist_1
            # short_unstuck_loss_allowance_pct
            if "edit_opt_v7_short_unstuck_loss_allowance_pct_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_unstuck_loss_allowance_pct_0 != self.config.optimize.bounds.short_unstuck_loss_allowance_pct_0:
                    self.config.optimize.bounds.short_unstuck_loss_allowance_pct_0 = st.session_state.edit_opt_v7_short_unstuck_loss_allowance_pct_0
            if "edit_opt_v7_short_unstuck_loss_allowance_pct_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_unstuck_loss_allowance_pct_1 != self.config.optimize.bounds.short_unstuck_loss_allowance_pct_1:
                    self.config.optimize.bounds.short_unstuck_loss_allowance_pct_1 = st.session_state.edit_opt_v7_short_unstuck_loss_allowance_pct_1
            # short_unstuck_threshold
            if "edit_opt_v7_short_unstuck_threshold_0" in st.session_state:
                if st.session_state.edit_opt_v7_short_unstuck_threshold_0 != self.config.optimize.bounds.short_unstuck_threshold_0:
                    self.config.optimize.bounds.short_unstuck_threshold_0 = st.session_state.edit_opt_v7_short_unstuck_threshold_0
            if "edit_opt_v7_short_unstuck_threshold_1" in st.session_state:
                if st.session_state.edit_opt_v7_short_unstuck_threshold_1 != self.config.optimize.bounds.short_unstuck_threshold_1:
                    self.config.optimize.bounds.short_unstuck_threshold_1 = st.session_state.edit_opt_v7_short_unstuck_threshold_1
            st.number_input("short_close_grid_markup_range min", min_value=Bounds.CLOSE_GRID_MARKUP_RANGE_MIN, max_value=float(round(self.config.optimize.bounds.short_close_grid_markup_range_1, Bounds.CLOSE_GRID_MARKUP_RANGE_ROUND)), value=float(round(self.config.optimize.bounds.short_close_grid_markup_range_0, Bounds.CLOSE_GRID_MARKUP_RANGE_ROUND)), step=Bounds.CLOSE_GRID_MARKUP_RANGE_STEP, format=Bounds.CLOSE_GRID_MARKUP_RANGE_FORMAT, key="edit_opt_v7_short_close_grid_markup_range_0", help=pbgui_help.close_grid_parameters)
            st.number_input("short_close_grid_min_markup min", min_value=Bounds.CLOSE_GRID_MIN_MARKUP_MIN, max_value=float(round(self.config.optimize.bounds.short_close_grid_min_markup_1, Bounds.CLOSE_GRID_MIN_MARKUP_ROUND)), value=float(round(self.config.optimize.bounds.short_close_grid_min_markup_0, Bounds.CLOSE_GRID_MIN_MARKUP_ROUND)), step=Bounds.CLOSE_GRID_MIN_MARKUP_STEP, format=Bounds.CLOSE_GRID_MIN_MARKUP_FORMAT, key="edit_opt_v7_short_close_grid_min_markup_0", help=pbgui_help.close_grid_parameters)
            st.number_input("short_close_grid_qty_pct min", min_value=Bounds.CLOSE_GRID_QTY_PCT_MIN, max_value=float(round(self.config.optimize.bounds.short_close_grid_qty_pct_1, Bounds.CLOSE_GRID_QTY_PCT_ROUND)), value=float(round(self.config.optimize.bounds.short_close_grid_qty_pct_0, Bounds.CLOSE_GRID_QTY_PCT_ROUND)), step=Bounds.CLOSE_GRID_QTY_PCT_STEP, format=Bounds.CLOSE_GRID_QTY_PCT_FORMAT, key="edit_opt_v7_short_close_grid_qty_pct_0", help=pbgui_help.close_grid_parameters)
            st.number_input("short_close_trailing_grid_ratio min", min_value=Bounds.CLOSE_TRAILING_GRID_RATIO_MIN, max_value=float(round(self.config.optimize.bounds.short_close_trailing_grid_ratio_1, Bounds.CLOSE_TRAILING_GRID_RATIO_ROUND)), value=float(round(self.config.optimize.bounds.short_close_trailing_grid_ratio_0, Bounds.CLOSE_TRAILING_GRID_RATIO_ROUND)), step=Bounds.CLOSE_TRAILING_GRID_RATIO_STEP, format=Bounds.CLOSE_TRAILING_GRID_RATIO_FORMAT, key="edit_opt_v7_short_close_trailing_grid_ratio_0", help=pbgui_help.trailing_parameters)
            st.number_input("short_close_trailing_qty_pct min", min_value=Bounds.CLOSE_TRAILING_QTY_PCT_MIN, max_value=float(round(self.config.optimize.bounds.short_close_trailing_qty_pct_1, Bounds.CLOSE_TRAILING_QTY_PCT_ROUND)), value=float(round(self.config.optimize.bounds.short_close_trailing_qty_pct_0, Bounds.CLOSE_TRAILING_QTY_PCT_ROUND)), step=Bounds.CLOSE_TRAILING_QTY_PCT_STEP, format=Bounds.CLOSE_TRAILING_QTY_PCT_FORMAT, key="edit_opt_v7_short_close_trailing_qty_pct_0", help=pbgui_help.trailing_parameters)
            st.number_input("short_close_trailing_retracement_pct min", min_value=Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_MIN, max_value=float(round(self.config.optimize.bounds.short_close_trailing_retracement_pct_1, Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_ROUND)), value=float(round(self.config.optimize.bounds.short_close_trailing_retracement_pct_0, Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_ROUND)), step=Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_STEP, format=Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_FORMAT, key="edit_opt_v7_short_close_trailing_retracement_pct_0", help=pbgui_help.trailing_parameters)
            st.number_input("short_close_trailing_threshold_pct min", min_value=Bounds.CLOSE_TRAILING_THRESHOLD_PCT_MIN, max_value=float(round(self.config.optimize.bounds.short_close_trailing_threshold_pct_1, Bounds.CLOSE_TRAILING_THRESHOLD_PCT_ROUND)), value=float(round(self.config.optimize.bounds.short_close_trailing_threshold_pct_0, Bounds.CLOSE_TRAILING_THRESHOLD_PCT_ROUND)), step=Bounds.CLOSE_TRAILING_THRESHOLD_PCT_STEP, format=Bounds.CLOSE_TRAILING_THRESHOLD_PCT_FORMAT, key="edit_opt_v7_short_close_trailing_threshold_pct_0", help=pbgui_help.trailing_parameters)
            st.number_input("short_ema_span_0 min", min_value=Bounds.EMA_SPAN_0_MIN, max_value=float(round(self.config.optimize.bounds.short_ema_span_0_1)), value=float(round(self.config.optimize.bounds.short_ema_span_0_0, Bounds.EMA_SPAN_0_ROUND)), step=Bounds.EMA_SPAN_0_STEP, format=Bounds.EMA_SPAN_0_FORMAT, key="edit_opt_v7_short_ema_span_0_0", help=pbgui_help.ema_span)
            st.number_input("short_ema_span_1 min", min_value=Bounds.EMA_SPAN_1_MIN, max_value=float(round(self.config.optimize.bounds.short_ema_span_1_1)), value=float(round(self.config.optimize.bounds.short_ema_span_1_0, Bounds.EMA_SPAN_1_ROUND)), step=Bounds.EMA_SPAN_1_STEP, format=Bounds.EMA_SPAN_1_FORMAT, key="edit_opt_v7_short_ema_span_1_0", help=pbgui_help.ema_span)
            st.number_input("short_entry_grid_double_down_factor min", min_value=Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_MIN, max_value=float(round(self.config.optimize.bounds.short_entry_grid_double_down_factor_1, Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_ROUND)), value=float(round(self.config.optimize.bounds.short_entry_grid_double_down_factor_0, Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_ROUND)), step=Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_STEP, format=Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_FORMAT, key="edit_opt_v7_short_entry_grid_double_down_factor_0", help=pbgui_help.entry_grid_double_down_factor)
            st.number_input("short_entry_grid_spacing_pct min", min_value=Bounds.ENTRY_GRID_SPACING_PCT_MIN, max_value=float(round(self.config.optimize.bounds.short_entry_grid_spacing_pct_1, Bounds.ENTRY_GRID_SPACING_PCT_ROUND)), value=float(round(self.config.optimize.bounds.short_entry_grid_spacing_pct_0, Bounds.ENTRY_GRID_SPACING_PCT_ROUND)), step=Bounds.ENTRY_GRID_SPACING_PCT_STEP, format=Bounds.ENTRY_GRID_SPACING_PCT_FORMAT, key="edit_opt_v7_short_entry_grid_spacing_pct_0", help=pbgui_help.entry_grid_spacing)
            st.number_input("short_entry_grid_spacing_weight min", min_value=Bounds.ENTRY_GRID_SPACING_WEIGHT_MIN, max_value=float(round(self.config.optimize.bounds.short_entry_grid_spacing_weight_1, Bounds.ENTRY_GRID_SPACING_WEIGHT_ROUND)), value=float(round(self.config.optimize.bounds.short_entry_grid_spacing_weight_0, Bounds.ENTRY_GRID_SPACING_WEIGHT_ROUND)), step=Bounds.ENTRY_GRID_SPACING_WEIGHT_STEP, format=Bounds.ENTRY_GRID_SPACING_WEIGHT_FORMAT, key="edit_opt_v7_short_entry_grid_spacing_weight_0", help=pbgui_help.entry_grid_spacing)
            st.number_input("short_entry_initial_ema_dist min", min_value=Bounds.ENTRY_INITIAL_EMA_DIST_MIN, max_value=float(round(self.config.optimize.bounds.short_entry_initial_ema_dist_1, Bounds.ENTRY_INITIAL_EMA_DIST_ROUND)), value=float(round(self.config.optimize.bounds.short_entry_initial_ema_dist_0, Bounds.ENTRY_INITIAL_EMA_DIST_ROUND)), step=Bounds.ENTRY_INITIAL_EMA_DIST_STEP, format=Bounds.ENTRY_INITIAL_EMA_DIST_FORMAT, key="edit_opt_v7_short_entry_initial_ema_dist_0", help=pbgui_help.entry_initial_ema_dist)
            st.number_input("short_entry_initial_qty_pct min", min_value=Bounds.ENTRY_INITIAL_QTY_PCT_MIN, max_value=float(round(self.config.optimize.bounds.short_entry_initial_qty_pct_1, Bounds.ENTRY_INITIAL_QTY_PCT_ROUND)), value=float(round(self.config.optimize.bounds.short_entry_initial_qty_pct_0, Bounds.ENTRY_INITIAL_QTY_PCT_ROUND)), step=Bounds.ENTRY_INITIAL_QTY_PCT_STEP, format=Bounds.ENTRY_INITIAL_QTY_PCT_FORMAT, key="edit_opt_v7_short_entry_initial_qty_pct_0", help=pbgui_help.entry_initial_qty_pct)
            st.number_input("short_entry_trailing_grid_ratio min", min_value=Bounds.ENTRY_TRAILING_GRID_RATIO_MIN, max_value=float(round(self.config.optimize.bounds.short_entry_trailing_grid_ratio_1, Bounds.ENTRY_TRAILING_GRID_RATIO_ROUND)), value=float(round(self.config.optimize.bounds.short_entry_trailing_grid_ratio_0, Bounds.ENTRY_TRAILING_GRID_RATIO_ROUND)), step=Bounds.ENTRY_TRAILING_GRID_RATIO_STEP, format=Bounds.ENTRY_TRAILING_GRID_RATIO_FORMAT, key="edit_opt_v7_short_entry_trailing_grid_ratio_0", help=pbgui_help.trailing_parameters)
            st.number_input("short_entry_trailing_retracement_pct min", min_value=Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_MIN, max_value=float(round(self.config.optimize.bounds.short_entry_trailing_retracement_pct_1, Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_ROUND)), value=float(round(self.config.optimize.bounds.short_entry_trailing_retracement_pct_0, Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_ROUND)), step=Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_STEP, format=Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_FORMAT, key="edit_opt_v7_short_entry_trailing_retracement_pct_0", help=pbgui_help.trailing_parameters)
            st.number_input("short_entry_trailing_threshold_pct min", min_value=Bounds.ENTRY_TRAILING_THRESHOLD_PCT_MIN, max_value=float(round(self.config.optimize.bounds.short_entry_trailing_threshold_pct_1, Bounds.ENTRY_TRAILING_THRESHOLD_PCT_ROUND)), value=float(round(self.config.optimize.bounds.short_entry_trailing_threshold_pct_0, Bounds.ENTRY_TRAILING_THRESHOLD_PCT_ROUND)), step=Bounds.ENTRY_TRAILING_THRESHOLD_PCT_STEP, format=Bounds.ENTRY_TRAILING_THRESHOLD_PCT_FORMAT, key="edit_opt_v7_short_entry_trailing_threshold_pct_0", help=pbgui_help.trailing_parameters)
            st.number_input("short_filter_relative_volume_clip_pct min", min_value=Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_MIN, max_value=float(round(self.config.optimize.bounds.short_filter_relative_volume_clip_pct_1, Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_ROUND)), value=float(round(self.config.optimize.bounds.short_filter_relative_volume_clip_pct_0, Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_ROUND)), step=Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_STEP, format=Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_FORMAT, key="edit_opt_v7_short_filter_relative_volume_clip_pct_0", help=pbgui_help.filter_relative_volume_clip_pct)
            st.number_input("short_filter_rolling_window min", min_value=Bounds.FILTER_ROLLING_WINDOW_MIN, max_value=float(round(self.config.optimize.bounds.short_filter_rolling_window_1)), value=float(round(self.config.optimize.bounds.short_filter_rolling_window_0, Bounds.FILTER_ROLLING_WINDOW_ROUND)), step=Bounds.FILTER_ROLLING_WINDOW_STEP, format=Bounds.FILTER_ROLLING_WINDOW_FORMAT, key="edit_opt_v7_short_filter_rolling_window_0", help=pbgui_help.filter_rolling_window)
            st.number_input("short_n_positions min", min_value=Bounds.N_POSITIONS_MIN, max_value=float(round(self.config.optimize.bounds.short_n_positions_1, Bounds.N_POSITIONS_ROUND)), value=float(round(self.config.optimize.bounds.short_n_positions_0, Bounds.N_POSITIONS_ROUND)), step=Bounds.N_POSITIONS_STEP, format=Bounds.N_POSITIONS_FORMAT, key="edit_opt_v7_short_n_positions_0", help=pbgui_help.n_positions)
            st.number_input("short_total_wallet_exposure_limit min", min_value=Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_MIN, max_value=float(round(self.config.optimize.bounds.short_total_wallet_exposure_limit_1, Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_ROUND)), value=float(round(self.config.optimize.bounds.short_total_wallet_exposure_limit_0, Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_ROUND)), step=Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_STEP, format=Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_FORMAT, key="edit_opt_v7_short_total_wallet_exposure_limit_0", help=pbgui_help.total_wallet_exposure_limit)
            st.number_input("short_unstuck_close_pct min", min_value=Bounds.UNSTUCK_CLOSE_PCT_MIN, max_value=float(round(self.config.optimize.bounds.short_unstuck_close_pct_1, Bounds.UNSTUCK_CLOSE_PCT_ROUND)), value=float(round(self.config.optimize.bounds.short_unstuck_close_pct_0, Bounds.UNSTUCK_CLOSE_PCT_ROUND)), step=Bounds.UNSTUCK_CLOSE_PCT_STEP, format=Bounds.UNSTUCK_CLOSE_PCT_FORMAT, key="edit_opt_v7_short_unstuck_close_pct_0", help=pbgui_help.unstuck_close_pct)
            st.number_input("short_unstuck_ema_dist min", min_value=Bounds.UNSTUCK_EMA_DIST_MIN, max_value=float(round(self.config.optimize.bounds.short_unstuck_ema_dist_1)), value=float(round(self.config.optimize.bounds.short_unstuck_ema_dist_0, Bounds.UNSTUCK_EMA_DIST_ROUND)), step=Bounds.UNSTUCK_EMA_DIST_STEP, format=Bounds.UNSTUCK_EMA_DIST_FORMAT, key="edit_opt_v7_short_unstuck_ema_dist_0", help=pbgui_help.unstuck_ema_dist)
            st.number_input("short_unstuck_loss_allowance_pct min", min_value=Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_MIN, max_value=float(round(self.config.optimize.bounds.short_unstuck_loss_allowance_pct_1, Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_ROUND)), value=float(round(self.config.optimize.bounds.short_unstuck_loss_allowance_pct_0, Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_ROUND)), step=Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_STEP, format=Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_FORMAT, key="edit_opt_v7_short_unstuck_loss_allowance_pct_0", help=pbgui_help.unstuck_loss_allowance_pct)
            st.number_input("short_unstuck_threshold min", min_value=Bounds.UNSTUCK_THRESHOLD_MIN, max_value=float(round(self.config.optimize.bounds.short_unstuck_threshold_1, Bounds.UNSTUCK_THRESHOLD_ROUND)), value=float(round(self.config.optimize.bounds.short_unstuck_threshold_0, Bounds.UNSTUCK_THRESHOLD_ROUND)), step=Bounds.UNSTUCK_THRESHOLD_STEP, format=Bounds.UNSTUCK_THRESHOLD_FORMAT, key="edit_opt_v7_short_unstuck_threshold_0", help=pbgui_help.unstuck_threshold)
        with col4:
            st.number_input("short_close_grid_markup_range max", min_value=float(round(self.config.optimize.bounds.short_close_grid_markup_range_0, Bounds.CLOSE_GRID_MARKUP_RANGE_ROUND)), max_value=Bounds.CLOSE_GRID_MARKUP_RANGE_MAX, value=float(round(self.config.optimize.bounds.short_close_grid_markup_range_1, Bounds.CLOSE_GRID_MARKUP_RANGE_ROUND)), step=Bounds.CLOSE_GRID_MARKUP_RANGE_STEP, format=Bounds.CLOSE_GRID_MARKUP_RANGE_FORMAT, key="edit_opt_v7_short_close_grid_markup_range_1", help=pbgui_help.close_grid_parameters)
            st.number_input("short_close_grid_min_markup max", min_value=float(round(self.config.optimize.bounds.short_close_grid_min_markup_0, Bounds.CLOSE_GRID_MIN_MARKUP_ROUND)), max_value=Bounds.CLOSE_GRID_MIN_MARKUP_MAX, value=float(round(self.config.optimize.bounds.short_close_grid_min_markup_1, Bounds.CLOSE_GRID_MIN_MARKUP_ROUND)), step=Bounds.CLOSE_GRID_MIN_MARKUP_STEP, format=Bounds.CLOSE_GRID_MIN_MARKUP_FORMAT, key="edit_opt_v7_short_close_grid_min_markup_1", help=pbgui_help.close_grid_parameters)
            st.number_input("short_close_grid_qty_pct max", min_value=float(round(self.config.optimize.bounds.short_close_grid_qty_pct_0, Bounds.CLOSE_GRID_QTY_PCT_ROUND)), max_value=Bounds.CLOSE_GRID_QTY_PCT_MAX, value=float(round(self.config.optimize.bounds.short_close_grid_qty_pct_1, Bounds.CLOSE_GRID_QTY_PCT_ROUND)), step=Bounds.CLOSE_GRID_QTY_PCT_STEP, format=Bounds.CLOSE_GRID_QTY_PCT_FORMAT, key="edit_opt_v7_short_close_grid_qty_pct_1", help=pbgui_help.close_grid_parameters)
            st.number_input("short_close_trailing_grid_ratio max", min_value=float(round(self.config.optimize.bounds.short_close_trailing_grid_ratio_0, Bounds.CLOSE_TRAILING_GRID_RATIO_ROUND)), max_value=Bounds.CLOSE_TRAILING_GRID_RATIO_MAX, value=float(round(self.config.optimize.bounds.short_close_trailing_grid_ratio_1, Bounds.CLOSE_TRAILING_GRID_RATIO_ROUND)), step=Bounds.CLOSE_TRAILING_GRID_RATIO_STEP, format=Bounds.CLOSE_TRAILING_GRID_RATIO_FORMAT, key="edit_opt_v7_short_close_trailing_grid_ratio_1", help=pbgui_help.trailing_parameters)
            st.number_input("short_close_trailing_qty_pct max", min_value=float(round(self.config.optimize.bounds.short_close_trailing_qty_pct_0, Bounds.CLOSE_TRAILING_QTY_PCT_ROUND)), max_value=Bounds.CLOSE_TRAILING_QTY_PCT_MAX, value=float(round(self.config.optimize.bounds.short_close_trailing_qty_pct_1, Bounds.CLOSE_TRAILING_QTY_PCT_ROUND)), step=Bounds.CLOSE_TRAILING_QTY_PCT_STEP, format=Bounds.CLOSE_TRAILING_QTY_PCT_FORMAT, key="edit_opt_v7_short_close_trailing_qty_pct_1", help=pbgui_help.trailing_parameters)
            st.number_input("short_close_trailing_retracement_pct max", min_value=float(round(self.config.optimize.bounds.short_close_trailing_retracement_pct_0, Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_ROUND)), max_value=Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_MAX, value=float(round(self.config.optimize.bounds.short_close_trailing_retracement_pct_1, Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_ROUND)), step=Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_STEP, format=Bounds.CLOSE_TRAILING_RETRACEMENT_PCT_FORMAT, key="edit_opt_v7_short_close_trailing_retracement_pct_1", help=pbgui_help.trailing_parameters)
            st.number_input("short_close_trailing_threshold_pct max", min_value=float(round(self.config.optimize.bounds.short_close_trailing_threshold_pct_0, Bounds.CLOSE_TRAILING_THRESHOLD_PCT_ROUND)), max_value=Bounds.CLOSE_TRAILING_THRESHOLD_PCT_MAX, value=float(round(self.config.optimize.bounds.short_close_trailing_threshold_pct_1, Bounds.CLOSE_TRAILING_THRESHOLD_PCT_ROUND)), step=Bounds.CLOSE_TRAILING_THRESHOLD_PCT_STEP, format=Bounds.CLOSE_TRAILING_THRESHOLD_PCT_FORMAT, key="edit_opt_v7_short_close_trailing_threshold_pct_1", help=pbgui_help.trailing_parameters)
            st.number_input("short_ema_span_0 max", min_value=float(round(self.config.optimize.bounds.short_ema_span_0_0, Bounds.EMA_SPAN_0_ROUND)), max_value=Bounds.EMA_SPAN_0_MAX, value=float(round(self.config.optimize.bounds.short_ema_span_0_1, Bounds.EMA_SPAN_0_ROUND)), step=Bounds.EMA_SPAN_0_STEP, format=Bounds.EMA_SPAN_0_FORMAT, key="edit_opt_v7_short_ema_span_0_1", help=pbgui_help.ema_span)
            st.number_input("short_ema_span_1 max", min_value=float(round(self.config.optimize.bounds.short_ema_span_1_0, Bounds.EMA_SPAN_1_ROUND)), max_value=Bounds.EMA_SPAN_1_MAX, value=float(round(self.config.optimize.bounds.short_ema_span_1_1, Bounds.EMA_SPAN_1_ROUND)), step=Bounds.EMA_SPAN_1_STEP, format=Bounds.EMA_SPAN_1_FORMAT, key="edit_opt_v7_short_ema_span_1_1", help=pbgui_help.ema_span)
            st.number_input("short_entry_grid_double_down_factor max", min_value=float(round(self.config.optimize.bounds.short_entry_grid_double_down_factor_0, Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_ROUND)), max_value=Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_MAX, value=float(round(self.config.optimize.bounds.short_entry_grid_double_down_factor_1, Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_ROUND)), step=Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_STEP, format=Bounds.ENTRY_GRID_DOUBLE_DOWN_FACTOR_FORMAT, key="edit_opt_v7_short_entry_grid_double_down_factor_1", help=pbgui_help.entry_grid_double_down_factor)
            st.number_input("short_entry_grid_spacing_pct max", min_value=float(round(self.config.optimize.bounds.short_entry_grid_spacing_pct_0, Bounds.ENTRY_GRID_SPACING_PCT_ROUND)), max_value=Bounds.ENTRY_GRID_SPACING_PCT_MAX, value=float(round(self.config.optimize.bounds.short_entry_grid_spacing_pct_1, Bounds.ENTRY_GRID_SPACING_PCT_ROUND)), step=Bounds.ENTRY_GRID_SPACING_PCT_STEP, format=Bounds.ENTRY_GRID_SPACING_PCT_FORMAT, key="edit_opt_v7_short_entry_grid_spacing_pct_1", help=pbgui_help.entry_grid_spacing)
            st.number_input("short_entry_grid_spacing_weight max", min_value=float(round(self.config.optimize.bounds.short_entry_grid_spacing_weight_0, Bounds.ENTRY_GRID_SPACING_WEIGHT_ROUND)), max_value=Bounds.ENTRY_GRID_SPACING_WEIGHT_MAX, value=float(round(self.config.optimize.bounds.short_entry_grid_spacing_weight_1, Bounds.ENTRY_GRID_SPACING_WEIGHT_ROUND)), step=Bounds.ENTRY_GRID_SPACING_WEIGHT_STEP, format=Bounds.ENTRY_GRID_SPACING_WEIGHT_FORMAT, key="edit_opt_v7_short_entry_grid_spacing_weight_1", help=pbgui_help.entry_grid_spacing)
            st.number_input("short_entry_initial_ema_dist max", min_value=float(round(self.config.optimize.bounds.short_entry_initial_ema_dist_0, Bounds.ENTRY_INITIAL_EMA_DIST_ROUND)), max_value=Bounds.ENTRY_INITIAL_EMA_DIST_MAX, value=float(round(self.config.optimize.bounds.short_entry_initial_ema_dist_1, Bounds.ENTRY_INITIAL_EMA_DIST_ROUND)), step=Bounds.ENTRY_INITIAL_EMA_DIST_STEP, format=Bounds.ENTRY_INITIAL_EMA_DIST_FORMAT, key="edit_opt_v7_short_entry_initial_ema_dist_1", help=pbgui_help.entry_initial_ema_dist)
            st.number_input("short_entry_initial_qty_pct max", min_value=float(round(self.config.optimize.bounds.short_entry_initial_qty_pct_0, Bounds.ENTRY_INITIAL_QTY_PCT_ROUND)), max_value=Bounds.ENTRY_INITIAL_QTY_PCT_MAX, value=float(round(self.config.optimize.bounds.short_entry_initial_qty_pct_1, Bounds.ENTRY_INITIAL_QTY_PCT_ROUND)), step=Bounds.ENTRY_INITIAL_QTY_PCT_STEP, format=Bounds.ENTRY_INITIAL_QTY_PCT_FORMAT, key="edit_opt_v7_short_entry_initial_qty_pct_1", help=pbgui_help.entry_initial_qty_pct)
            st.number_input("short_entry_trailing_grid_ratio max", min_value=float(round(self.config.optimize.bounds.short_entry_trailing_grid_ratio_0, Bounds.ENTRY_TRAILING_GRID_RATIO_ROUND)), max_value=Bounds.ENTRY_TRAILING_GRID_RATIO_MAX, value=float(round(self.config.optimize.bounds.short_entry_trailing_grid_ratio_1, Bounds.ENTRY_TRAILING_GRID_RATIO_ROUND)), step=Bounds.ENTRY_TRAILING_GRID_RATIO_STEP, format=Bounds.ENTRY_TRAILING_GRID_RATIO_FORMAT, key="edit_opt_v7_short_entry_trailing_grid_ratio_1", help=pbgui_help.trailing_parameters)
            st.number_input("short_entry_trailing_retracement_pct max", min_value=float(round(self.config.optimize.bounds.short_entry_trailing_retracement_pct_0, Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_ROUND)), max_value=Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_MAX, value=float(round(self.config.optimize.bounds.short_entry_trailing_retracement_pct_1, Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_ROUND)), step=Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_STEP, format=Bounds.ENTRY_TRAILING_RETRACEMENT_PCT_FORMAT, key="edit_opt_v7_short_entry_trailing_retracement_pct_1", help=pbgui_help.trailing_parameters)
            st.number_input("short_entry_trailing_threshold_pct max", min_value=float(round(self.config.optimize.bounds.short_entry_trailing_threshold_pct_0, Bounds.ENTRY_TRAILING_THRESHOLD_PCT_ROUND)), max_value=Bounds.ENTRY_TRAILING_THRESHOLD_PCT_MAX, value=float(round(self.config.optimize.bounds.short_entry_trailing_threshold_pct_1, Bounds.ENTRY_TRAILING_THRESHOLD_PCT_ROUND)), step=Bounds.ENTRY_TRAILING_THRESHOLD_PCT_STEP, format=Bounds.ENTRY_TRAILING_THRESHOLD_PCT_FORMAT, key="edit_opt_v7_short_entry_trailing_threshold_pct_1", help=pbgui_help.trailing_parameters)
            st.number_input("short_filter_relative_volume_clip_pct max", min_value=float(round(self.config.optimize.bounds.short_filter_relative_volume_clip_pct_0, Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_ROUND)), max_value=Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_MAX, value=float(round(self.config.optimize.bounds.short_filter_relative_volume_clip_pct_1, Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_ROUND)), step=Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_STEP, format=Bounds.FILTER_RELATIVE_VOLUME_CLIP_PCT_FORMAT, key="edit_opt_v7_short_filter_relative_volume_clip_pct_1", help=pbgui_help.filter_relative_volume_clip_pct)
            st.number_input("short_filter_rolling_window max", min_value=float(round(self.config.optimize.bounds.short_filter_rolling_window_0, Bounds.FILTER_ROLLING_WINDOW_ROUND)), max_value=Bounds.FILTER_ROLLING_WINDOW_MAX, value=float(round(self.config.optimize.bounds.short_filter_rolling_window_1, Bounds.FILTER_ROLLING_WINDOW_ROUND)), step=Bounds.FILTER_ROLLING_WINDOW_STEP, format=Bounds.FILTER_ROLLING_WINDOW_FORMAT, key="edit_opt_v7_short_filter_rolling_window_1", help=pbgui_help.filter_rolling_window)
            st.number_input("short_n_positions max", min_value=float(round(self.config.optimize.bounds.short_n_positions_0, Bounds.N_POSITIONS_ROUND)), max_value=Bounds.N_POSITIONS_MAX, value=float(round(self.config.optimize.bounds.short_n_positions_1, Bounds.N_POSITIONS_ROUND)), step=Bounds.N_POSITIONS_STEP, format=Bounds.N_POSITIONS_FORMAT, key="edit_opt_v7_short_n_positions_1", help=pbgui_help.n_positions)
            st.number_input("short_total_wallet_exposure_limit max", min_value=float(round(self.config.optimize.bounds.short_total_wallet_exposure_limit_0, Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_ROUND)), max_value=Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_MAX, value=float(round(self.config.optimize.bounds.short_total_wallet_exposure_limit_1, Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_ROUND)), step=Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_STEP, format=Bounds.TOTAL_WALLET_EXPOSURE_LIMIT_FORMAT, key="edit_opt_v7_short_total_wallet_exposure_limit_1", help=pbgui_help.total_wallet_exposure_limit)
            st.number_input("short_unstuck_close_pct max", min_value=float(round(self.config.optimize.bounds.short_unstuck_close_pct_0, Bounds.UNSTUCK_CLOSE_PCT_ROUND)), max_value=Bounds.UNSTUCK_CLOSE_PCT_MAX, value=float(round(self.config.optimize.bounds.short_unstuck_close_pct_1, Bounds.UNSTUCK_CLOSE_PCT_ROUND)), step=Bounds.UNSTUCK_CLOSE_PCT_STEP, format=Bounds.UNSTUCK_CLOSE_PCT_FORMAT, key="edit_opt_v7_short_unstuck_close_pct_1", help=pbgui_help.unstuck_close_pct)
            st.number_input("short_unstuck_ema_dist max", min_value=float(round(self.config.optimize.bounds.short_unstuck_ema_dist_0, Bounds.UNSTUCK_EMA_DIST_ROUND)), max_value=Bounds.UNSTUCK_EMA_DIST_MAX, value=float(round(self.config.optimize.bounds.short_unstuck_ema_dist_1, Bounds.UNSTUCK_EMA_DIST_ROUND)), step=Bounds.UNSTUCK_EMA_DIST_STEP, format=Bounds.UNSTUCK_EMA_DIST_FORMAT, key="edit_opt_v7_short_unstuck_ema_dist_1", help=pbgui_help.unstuck_ema_dist)
            st.number_input("short_unstuck_loss_allowance_pct max", min_value=float(round(self.config.optimize.bounds.short_unstuck_loss_allowance_pct_0, Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_ROUND)), max_value=Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_MAX, value=float(round(self.config.optimize.bounds.short_unstuck_loss_allowance_pct_1, Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_ROUND)), step=Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_STEP, format=Bounds.UNSTUCK_LOSS_ALLOWANCE_PCT_FORMAT, key="edit_opt_v7_short_unstuck_loss_allowance_pct_1", help=pbgui_help.unstuck_loss_allowance_pct)
            st.number_input("short_unstuck_threshold max", min_value=float(round(self.config.optimize.bounds.short_unstuck_threshold_0, Bounds.UNSTUCK_THRESHOLD_ROUND)), max_value=Bounds.UNSTUCK_THRESHOLD_MAX, value=float(round(self.config.optimize.bounds.short_unstuck_threshold_1, Bounds.UNSTUCK_THRESHOLD_ROUND)), step=Bounds.UNSTUCK_THRESHOLD_STEP, format=Bounds.UNSTUCK_THRESHOLD_FORMAT, key="edit_opt_v7_short_unstuck_threshold_1", help=pbgui_help.unstuck_threshold)

    def save(self):
        self.path = Path(f'{PBGDIR}/data/opt_v7')
        if not self.path.exists():
            self.path.mkdir(parents=True)
        self.config.config_file = Path(f'{self.path}/{self.name}.json')
        self.config.save_config()

    def save_queue(self):
        dest = Path(f'{PBGDIR}/data/opt_v7_queue')
        unique_filename = str(uuid.uuid4())
        file = Path(f'{dest}/{unique_filename}.json') 
        opt_dict = {
            "name": self.name,
            "filename": unique_filename,
            "json": str(self.config.config_file),
            "exchange": self.config.backtest.exchange
        }
        dest.mkdir(parents=True, exist_ok=True)
        with open(file, "w", encoding='utf-8') as f:
            json.dump(opt_dict, f, indent=4)

    def remove(self):
        Path(self.config.config_file).unlink(missing_ok=True)

class OptimizesV7:
    def __init__(self):
        self.optimizes = []

    def view_optimizes(self):
        # Init
        if not self.optimizes:
            self.find_optimizes()
        if not "ed_key" in st.session_state:
            st.session_state.ed_key = 0
        ed_key = st.session_state.ed_key
        if f'select_optimize_v7_{ed_key}' in st.session_state:
            ed = st.session_state[f'select_optimize_v7_{ed_key}']
            for row in ed["edited_rows"]:
                if "edit" in ed["edited_rows"][row]:
                    st.session_state.opt_v7 = self.optimizes[row]
                    st.rerun()
                if 'delete' in ed["edited_rows"][row]:
                    if ed["edited_rows"][row]['delete']:
                        self.optimizes[row].remove()
                        self.optimizes.pop(row)
                        st.rerun()
        d = []
        for id, opt in enumerate(self.optimizes):
            d.append({
                'id': id,
                'edit': False,
                'Name': opt.name,
                'Exchange': opt.config.backtest.exchange,
                'delete' : False,
            })
        column_config = {
            "id": None,
            "edit": st.column_config.CheckboxColumn(label="Edit"),
            }
        #Display optimizes
        st.data_editor(data=d, height=36+(len(d))*35, use_container_width=True, key=f'select_optimize_v7_{ed_key}', hide_index=None, column_order=None, column_config=column_config, disabled=['id','name'])

    def find_optimizes(self):
        p = str(Path(f'{PBGDIR}/data/opt_v7/*.json'))
        found_opt = glob.glob(p, recursive=False)
        if found_opt:
            for p in found_opt:
                opt = OptimizeV7Item(p)
                self.optimizes.append(opt)
    
def main():
    # Disable Streamlit Warnings when running directly
    logging.getLogger("streamlit.runtime.state.session_state_proxy").disabled=True
    logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").disabled=True
    opt = OptimizeV7Queue()
    while True:
        opt.load()
        for item in opt.items:
            while opt.running():
                time.sleep(5)
            pb_config = configparser.ConfigParser()
            pb_config.read('pbgui.ini')
            if not eval(pb_config.get("optimize_v7", "autostart")):
                return
            if item.status() == "not started":
                print(f'{datetime.datetime.now().isoformat(sep=" ", timespec="seconds")} Optimizing {item.filename} started')
                item.run()
                time.sleep(1)
        time.sleep(60)

if __name__ == '__main__':
    main()