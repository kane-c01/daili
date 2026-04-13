import os
import json
from datetime import datetime, date
from functools import wraps

from flask import Flask, render_template, redirect, url_for, flash, request, jsonify, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user

from models import db, User, Tier, Transaction, Commission, SystemSetting, Gift, PointTransaction

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24).hex()
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///daili.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = '请先登录'


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def update_user_tier(user):
    """Auto-update user tier based on valid downline count."""
    count = user.get_valid_downline_count()
    tiers = Tier.query.order_by(Tier.sort_order.desc()).all()
    for t in tiers:
        if count >= t.min_downlines:
            if user.tier_id != t.id:
                old_tier = user.tier
                user.tier_id = t.id
                if old_tier and old_tier.sort_order < t.sort_order and t.upgrade_bonus > 0:
                    bonus = Transaction(
                        user_id=user.id, amount=t.upgrade_bonus,
                        currency=user.currency, transaction_type='bonus',
                        category='upgrade',
                        description=f'升级至 {t.name} 奖励'
                    )
                    db.session.add(bonus)
            break
    db.session.commit()


# ── Auth Routes ──

@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('agent_dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password) and user.is_active:
            login_user(user, remember=True)
            flash('登录成功', 'success')
            return redirect(url_for('index'))
        flash('用户名或密码错误', 'danger')
    return render_template('auth/login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('已退出登录', 'info')
    return redirect(url_for('login'))


# ── Admin Routes ──

@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    total_agents = User.query.filter_by(role='agent').count()
    active_agents = User.query.filter_by(role='agent', is_active=True).count()
    tiers = Tier.query.order_by(Tier.sort_order).all()
    tier_stats = []
    for t in tiers:
        count = User.query.filter_by(tier_id=t.id, role='agent').count()
        tier_stats.append({'tier': t, 'count': count})
    recent_agents = User.query.filter_by(role='agent').order_by(User.created_at.desc()).limit(10).all()
    return render_template('admin/dashboard.html',
                           total_agents=total_agents,
                           active_agents=active_agents,
                           tier_stats=tier_stats,
                           recent_agents=recent_agents)


@app.route('/admin/agents')
@login_required
@admin_required
def admin_agents():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    query = User.query.filter_by(role='agent')
    if search:
        query = query.filter(
            (User.name.contains(search)) |
            (User.username.contains(search)) |
            (User.phone.contains(search))
        )
    agents = query.order_by(User.created_at.desc()).paginate(page=page, per_page=20)
    tiers = Tier.query.order_by(Tier.sort_order).all()
    currencies = ['MYR', 'AUD', 'SGD']
    all_agents = User.query.filter_by(role='agent', is_active=True).all()
    return render_template('admin/agents.html', agents=agents, tiers=tiers,
                           currencies=currencies, search=search, all_agents=all_agents)


@app.route('/admin/agents/create', methods=['POST'])
@login_required
@admin_required
def admin_create_agent():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    name = request.form.get('name', '').strip()
    birthday_str = request.form.get('birthday', '')
    phone = request.form.get('phone', '').strip()
    bank_account = request.form.get('bank_account', '').strip()
    bank_name = request.form.get('bank_name', '').strip()
    email = request.form.get('email', '').strip()
    currency = request.form.get('currency', 'MYR')
    referrer_id = request.form.get('referrer_id', '', type=int) or None

    if User.query.filter_by(username=username).first():
        flash('用户名已存在', 'danger')
        return redirect(url_for('admin_agents'))

    birthday = None
    if birthday_str:
        try:
            birthday = datetime.strptime(birthday_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    default_tier = Tier.query.filter_by(sort_order=0).first()
    agent = User(
        username=username, name=name, role='agent',
        birthday=birthday, phone=phone,
        bank_account=bank_account, bank_name=bank_name,
        email=email, currency=currency,
        referrer_id=referrer_id,
        tier_id=default_tier.id if default_tier else None,
        is_verified=True
    )
    agent.set_password(password)
    db.session.add(agent)
    db.session.commit()

    if referrer_id:
        referrer = db.session.get(User, referrer_id)
        if referrer:
            update_user_tier(referrer)

    flash(f'代理 {name} 创建成功', 'success')
    return redirect(url_for('admin_agents'))


@app.route('/admin/agents/<int:agent_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_agent(agent_id):
    agent = db.session.get(User, agent_id)
    if not agent or agent.role != 'agent':
        abort(404)

    if request.method == 'POST':
        agent.name = request.form.get('name', '').strip()
        birthday_str = request.form.get('birthday', '')
        if birthday_str:
            try:
                agent.birthday = datetime.strptime(birthday_str, '%Y-%m-%d').date()
            except ValueError:
                pass
        agent.phone = request.form.get('phone', '').strip()
        agent.bank_account = request.form.get('bank_account', '').strip()
        agent.bank_name = request.form.get('bank_name', '').strip()
        agent.email = request.form.get('email', '').strip()
        agent.currency = request.form.get('currency', 'MYR')
        agent.is_active = request.form.get('is_active') == 'on'
        agent.is_verified = request.form.get('is_verified') == 'on'
        agent.total_deposit = float(request.form.get('total_deposit', 0))

        manual_tier_id = request.form.get('tier_id', type=int)
        if manual_tier_id:
            agent.tier_id = manual_tier_id

        new_password = request.form.get('new_password', '').strip()
        if new_password:
            agent.set_password(new_password)

        new_referrer_id = request.form.get('referrer_id', type=int) or None
        if new_referrer_id != agent.referrer_id:
            old_referrer_id = agent.referrer_id
            agent.referrer_id = new_referrer_id
            db.session.commit()
            if old_referrer_id:
                old_ref = db.session.get(User, old_referrer_id)
                if old_ref:
                    update_user_tier(old_ref)
            if new_referrer_id:
                new_ref = db.session.get(User, new_referrer_id)
                if new_ref:
                    update_user_tier(new_ref)
        else:
            db.session.commit()
        flash('代理信息已更新', 'success')
        return redirect(url_for('admin_agents'))

    tiers = Tier.query.order_by(Tier.sort_order).all()
    currencies = ['MYR', 'AUD', 'SGD']
    all_agents = User.query.filter(User.id != agent_id, User.role == 'agent', User.is_active == True).all()
    return render_template('admin/edit_agent.html', agent=agent, tiers=tiers,
                           currencies=currencies, all_agents=all_agents)


@app.route('/admin/agents/<int:agent_id>/toggle', methods=['POST'])
@login_required
@admin_required
def admin_toggle_agent(agent_id):
    agent = db.session.get(User, agent_id)
    if agent and agent.role == 'agent':
        agent.is_active = not agent.is_active
        db.session.commit()
        status = '启用' if agent.is_active else '禁用'
        flash(f'代理 {agent.name} 已{status}', 'success')
    return redirect(url_for('admin_agents'))


@app.route('/admin/tiers', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_tiers():
    if request.method == 'POST':
        tiers = Tier.query.order_by(Tier.sort_order).all()
        for t in tiers:
            prefix = f'tier_{t.id}_'
            t.min_downlines = int(request.form.get(f'{prefix}min', t.min_downlines))
            t.max_downlines = int(request.form.get(f'{prefix}max', t.max_downlines))
            t.live_casino_rate = float(request.form.get(f'{prefix}live_casino', t.live_casino_rate))
            t.slot_rate = float(request.form.get(f'{prefix}slot', t.slot_rate))
            t.birthday_bonus = float(request.form.get(f'{prefix}birthday', t.birthday_bonus))
            t.upgrade_bonus = float(request.form.get(f'{prefix}upgrade', t.upgrade_bonus))
            t.points_rate = float(request.form.get(f'{prefix}points_rate', t.points_rate))
        db.session.commit()

        agents = User.query.filter_by(role='agent').all()
        for a in agents:
            update_user_tier(a)

        flash('等级设置已更新', 'success')
        return redirect(url_for('admin_tiers'))

    tiers = Tier.query.order_by(Tier.sort_order).all()
    return render_template('admin/tiers.html', tiers=tiers)


@app.route('/admin/tree')
@login_required
@admin_required
def admin_tree():
    top_agents = User.query.filter_by(role='agent', referrer_id=None, is_active=True).all()
    tree_data = []
    for a in top_agents:
        tree_data.append({
            'id': a.id,
            'name': a.name,
            'username': a.username,
            'tier': a.tier.name if a.tier else 'Classic',
            'tier_icon': a.tier.icon_file if a.tier else 'classic.jpeg',
            'verified': a.is_verified,
            'deposit': a.total_deposit,
            'children': a.get_downline_tree()
        })
    return render_template('admin/tree.html', tree_data=json.dumps(tree_data, ensure_ascii=False))


@app.route('/admin/ranking')
@login_required
@admin_required
def admin_ranking():
    agents = User.query.filter_by(role='agent', is_active=True).all()
    ranking = []
    for a in agents:
        all_down = a.get_all_downlines()
        valid_count = sum(1 for d in all_down if d.is_verified and d.total_deposit >= 100)
        direct_count = User.query.filter_by(referrer_id=a.id, is_active=True).count()
        total_comm = db.session.query(db.func.sum(Commission.amount))\
            .filter_by(agent_id=a.id).scalar() or 0
        ranking.append({
            'agent': a,
            'total_downlines': len(all_down),
            'valid_downlines': valid_count,
            'direct_downlines': direct_count,
            'total_commission': round(total_comm, 2),
        })
    ranking.sort(key=lambda x: x['valid_downlines'], reverse=True)
    for i, r in enumerate(ranking):
        r['rank'] = i + 1
    return render_template('admin/ranking.html', ranking=ranking)


@app.route('/admin/commissions')
@login_required
@admin_required
def admin_commissions():
    page = request.args.get('page', 1, type=int)
    commissions = Commission.query.order_by(Commission.created_at.desc()).paginate(page=page, per_page=20)
    return render_template('admin/commissions.html', commissions=commissions)


@app.route('/admin/transactions')
@login_required
@admin_required
def admin_transactions():
    page = request.args.get('page', 1, type=int)
    transactions = Transaction.query.order_by(Transaction.created_at.desc()).paginate(page=page, per_page=20)
    return render_template('admin/transactions.html', transactions=transactions)


@app.route('/admin/add_deposit', methods=['POST'])
@login_required
@admin_required
def admin_add_deposit():
    agent_id = request.form.get('agent_id', type=int)
    amount = float(request.form.get('amount', 0))
    category = request.form.get('category', 'deposit')

    agent = db.session.get(User, agent_id)
    if not agent or agent.role != 'agent':
        flash('代理不存在', 'danger')
        return redirect(url_for('admin_agents'))

    agent.total_deposit += amount
    if agent.total_deposit >= 100:
        agent.is_verified = True

    tx = Transaction(
        user_id=agent.id, amount=amount, currency=agent.currency,
        transaction_type='deposit', category=category,
        description=f'充值 {amount} {agent.currency}'
    )
    db.session.add(tx)

    if category in ('live_casino', 'slot') and agent.referrer_id:
        referrer = db.session.get(User, agent.referrer_id)
        if referrer and referrer.tier:
            rate = referrer.tier.live_casino_rate if category == 'live_casino' else referrer.tier.slot_rate
            comm_amount = amount * rate / 100
            comm = Commission(
                agent_id=referrer.id, from_user_id=agent.id,
                amount=comm_amount, currency=agent.currency,
                category=category, turnover_amount=amount, rate=rate
            )
            db.session.add(comm)
            comm_tx = Transaction(
                user_id=referrer.id, amount=comm_amount, currency=agent.currency,
                transaction_type='commission', category=category,
                description=f'来自 {agent.name} 的 {category} 佣金'
            )
            db.session.add(comm_tx)

            if referrer.tier.points_rate > 0:
                points_earned = int(comm_amount * referrer.tier.points_rate / 100)
                if points_earned > 0:
                    referrer.points += points_earned
                    pt = PointTransaction(
                        user_id=referrer.id, points=points_earned,
                        balance_after=referrer.points, transaction_type='earn',
                        description=f'佣金转积分 ({referrer.tier.points_rate}%): {agent.name} {category}'
                    )
                    db.session.add(pt)

    db.session.commit()
    update_user_tier(agent)
    if agent.referrer_id:
        referrer = db.session.get(User, agent.referrer_id)
        if referrer:
            update_user_tier(referrer)

    flash(f'已为 {agent.name} 添加 {amount} {agent.currency} 充值', 'success')
    return redirect(url_for('admin_agents'))


# ── Admin Gift & Points Routes ──

@app.route('/admin/gifts')
@login_required
@admin_required
def admin_gifts():
    gifts = Gift.query.order_by(Gift.sort_order, Gift.id).all()
    return render_template('admin/gifts.html', gifts=gifts)


@app.route('/admin/gifts/create', methods=['POST'])
@login_required
@admin_required
def admin_create_gift():
    gift = Gift(
        name=request.form.get('name', '').strip(),
        description=request.form.get('description', '').strip(),
        points_required=int(request.form.get('points_required', 0)),
        stock=int(request.form.get('stock', 0)),
        image_url=request.form.get('image_url', '').strip(),
        sort_order=int(request.form.get('sort_order', 0)),
        is_active=True
    )
    db.session.add(gift)
    db.session.commit()
    flash(f'礼品 {gift.name} 创建成功', 'success')
    return redirect(url_for('admin_gifts'))


@app.route('/admin/gifts/<int:gift_id>/edit', methods=['POST'])
@login_required
@admin_required
def admin_edit_gift(gift_id):
    gift = db.session.get(Gift, gift_id)
    if not gift:
        abort(404)
    gift.name = request.form.get('name', '').strip()
    gift.description = request.form.get('description', '').strip()
    gift.points_required = int(request.form.get('points_required', 0))
    gift.stock = int(request.form.get('stock', 0))
    gift.image_url = request.form.get('image_url', '').strip()
    gift.sort_order = int(request.form.get('sort_order', 0))
    gift.is_active = request.form.get('is_active') == 'on'
    db.session.commit()
    flash(f'礼品 {gift.name} 已更新', 'success')
    return redirect(url_for('admin_gifts'))


@app.route('/admin/gifts/<int:gift_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_gift(gift_id):
    gift = db.session.get(Gift, gift_id)
    if gift:
        db.session.delete(gift)
        db.session.commit()
        flash('礼品已删除', 'success')
    return redirect(url_for('admin_gifts'))


@app.route('/admin/points')
@login_required
@admin_required
def admin_points():
    page = request.args.get('page', 1, type=int)
    transactions = PointTransaction.query.order_by(PointTransaction.created_at.desc()).paginate(page=page, per_page=20)
    agents = User.query.filter_by(role='agent', is_active=True).order_by(User.name).all()
    return render_template('admin/points.html', transactions=transactions, agents=agents)


@app.route('/admin/points/adjust', methods=['POST'])
@login_required
@admin_required
def admin_adjust_points():
    agent_id = request.form.get('agent_id', type=int)
    points = int(request.form.get('points', 0))
    action = request.form.get('action', 'add')
    reason = request.form.get('reason', '').strip() or '管理员操作'

    agent = db.session.get(User, agent_id)
    if not agent or agent.role != 'agent':
        flash('代理不存在', 'danger')
        return redirect(url_for('admin_points'))

    if action == 'deduct':
        if agent.points < points:
            flash(f'{agent.name} 积分不足（当前 {agent.points}）', 'danger')
            return redirect(url_for('admin_points'))
        agent.points -= points
        tx_type = 'admin_deduct'
        desc = f'管理员扣除: {reason}'
    else:
        agent.points += points
        tx_type = 'admin_add'
        desc = f'管理员发放: {reason}'

    pt = PointTransaction(
        user_id=agent.id, points=points if action == 'add' else -points,
        balance_after=agent.points, transaction_type=tx_type, description=desc
    )
    db.session.add(pt)
    db.session.commit()

    flash(f'已为 {agent.name} {"增加" if action == "add" else "扣除"} {points} 积分（余额: {agent.points}）', 'success')
    return redirect(url_for('admin_points'))


# ── Agent Routes ──

@app.route('/agent')
@login_required
def agent_dashboard():
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))

    valid_count = current_user.get_valid_downline_count()
    all_downlines = current_user.get_all_downlines()
    direct_downlines = User.query.filter_by(referrer_id=current_user.id).all()

    current_tier = current_user.tier
    next_tier = None
    progress = 0
    if current_tier:
        next_t = Tier.query.filter(Tier.sort_order > current_tier.sort_order).order_by(Tier.sort_order).first()
        if next_t:
            next_tier = next_t
            needed = next_t.min_downlines - valid_count
            total_range = next_t.min_downlines - current_tier.min_downlines
            if total_range > 0:
                progress = min(100, int((valid_count - current_tier.min_downlines) / total_range * 100))

    recent_commissions = Commission.query.filter_by(agent_id=current_user.id)\
        .order_by(Commission.created_at.desc()).limit(10).all()
    total_commission = db.session.query(db.func.sum(Commission.amount))\
        .filter_by(agent_id=current_user.id).scalar() or 0

    return render_template('agent/dashboard.html',
                           valid_count=valid_count,
                           all_downlines=all_downlines,
                           direct_downlines=direct_downlines,
                           current_tier=current_tier,
                           next_tier=next_tier,
                           progress=progress,
                           recent_commissions=recent_commissions,
                           total_commission=total_commission)


@app.route('/agent/downlines')
@login_required
def agent_downlines():
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    direct = User.query.filter_by(referrer_id=current_user.id).all()
    all_down = current_user.get_all_downlines()
    return render_template('agent/downlines.html', direct=direct, all_downlines=all_down)


@app.route('/agent/tree')
@login_required
def agent_tree():
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    tree_data = [{
        'id': current_user.id,
        'name': current_user.name,
        'username': current_user.username,
        'tier': current_user.tier.name if current_user.tier else 'Classic',
        'tier_icon': current_user.tier.icon_file if current_user.tier else 'classic.jpeg',
        'verified': current_user.is_verified,
        'deposit': current_user.total_deposit,
        'children': current_user.get_downline_tree()
    }]
    return render_template('agent/tree.html', tree_data=json.dumps(tree_data, ensure_ascii=False))


@app.route('/agent/commissions')
@login_required
def agent_commissions():
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    page = request.args.get('page', 1, type=int)
    commissions = Commission.query.filter_by(agent_id=current_user.id)\
        .order_by(Commission.created_at.desc()).paginate(page=page, per_page=20)
    total = db.session.query(db.func.sum(Commission.amount))\
        .filter_by(agent_id=current_user.id).scalar() or 0
    return render_template('agent/commissions.html', commissions=commissions, total=total)


@app.route('/agent/points')
@login_required
def agent_points():
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    gifts = Gift.query.filter_by(is_active=True).order_by(Gift.sort_order, Gift.id).all()
    page = request.args.get('page', 1, type=int)
    transactions = PointTransaction.query.filter_by(user_id=current_user.id)\
        .order_by(PointTransaction.created_at.desc()).paginate(page=page, per_page=15)
    return render_template('agent/points.html', gifts=gifts, transactions=transactions)


@app.route('/agent/redeem/<int:gift_id>', methods=['POST'])
@login_required
def agent_redeem(gift_id):
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))

    gift = db.session.get(Gift, gift_id)
    if not gift or not gift.is_active:
        flash('礼品不存在或已下架', 'danger')
        return redirect(url_for('agent_points'))

    if current_user.points < gift.points_required:
        flash(f'积分不足，需要 {gift.points_required} 积分，当前 {current_user.points} 积分', 'danger')
        return redirect(url_for('agent_points'))

    if gift.stock <= 0:
        flash('该礼品库存不足', 'danger')
        return redirect(url_for('agent_points'))

    current_user.points -= gift.points_required
    gift.stock -= 1

    pt = PointTransaction(
        user_id=current_user.id, points=-gift.points_required,
        balance_after=current_user.points, transaction_type='redeem',
        description=f'兑换礼品: {gift.name}', gift_id=gift.id
    )
    db.session.add(pt)
    db.session.commit()

    flash(f'成功兑换 {gift.name}！剩余积分: {current_user.points}', 'success')
    return redirect(url_for('agent_points'))


@app.route('/agent/profile', methods=['GET', 'POST'])
@login_required
def agent_profile():
    if current_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        if new_password:
            if new_password == confirm_password:
                current_user.set_password(new_password)
                db.session.commit()
                flash('密码修改成功', 'success')
            else:
                flash('两次密码不一致', 'danger')
        return redirect(url_for('agent_profile'))
    return render_template('agent/profile.html')


# ── API Endpoints ──

@app.route('/api/agent/<int:agent_id>/tree')
@login_required
def api_agent_tree(agent_id):
    agent = db.session.get(User, agent_id)
    if not agent:
        return jsonify({'error': 'not found'}), 404
    if not current_user.is_admin and current_user.id != agent_id:
        return jsonify({'error': 'forbidden'}), 403
    tree = [{
        'id': agent.id,
        'name': agent.name,
        'username': agent.username,
        'tier': agent.tier.name if agent.tier else 'Classic',
        'tier_icon': agent.tier.icon_file if agent.tier else 'classic.jpeg',
        'verified': agent.is_verified,
        'deposit': agent.total_deposit,
        'children': agent.get_downline_tree()
    }]
    return jsonify(tree)


@app.route('/api/agent/<int:agent_id>/info')
@login_required
def api_agent_info(agent_id):
    agent = db.session.get(User, agent_id)
    if not agent:
        return jsonify({'error': 'not found'}), 404
    if not current_user.is_admin and current_user.id != agent_id:
        all_down_ids = [d.id for d in current_user.get_all_downlines()]
        if agent_id not in all_down_ids:
            return jsonify({'error': 'forbidden'}), 403

    total_comm = db.session.query(db.func.sum(Commission.amount))\
        .filter_by(agent_id=agent.id).scalar() or 0
    direct_count = User.query.filter_by(referrer_id=agent.id, is_active=True).count()

    return jsonify({
        'id': agent.id,
        'username': agent.username,
        'name': agent.name,
        'phone': agent.phone or '-',
        'email': agent.email or '-',
        'birthday': agent.birthday.strftime('%Y-%m-%d') if agent.birthday else '-',
        'bank_name': agent.bank_name or '-',
        'bank_account': agent.bank_account or '-',
        'currency': agent.currency,
        'tier': agent.tier.name if agent.tier else 'Classic',
        'tier_icon': agent.tier.icon_file if agent.tier else 'classic.jpeg',
        'total_deposit': agent.total_deposit,
        'is_verified': agent.is_verified,
        'is_active': agent.is_active,
        'valid_downlines': agent.get_valid_downline_count(),
        'direct_downlines': direct_count,
        'total_commission': round(total_comm, 2),
        'referrer': agent.referrer.name if agent.referrer else '-',
        'created_at': agent.created_at.strftime('%Y-%m-%d %H:%M'),
        'live_casino_rate': agent.tier.live_casino_rate if agent.tier else 0,
        'slot_rate': agent.tier.slot_rate if agent.tier else 0,
        'birthday_bonus': agent.tier.birthday_bonus if agent.tier else 0,
        'upgrade_bonus': agent.tier.upgrade_bonus if agent.tier else 0,
        'points': agent.points,
    })


# ── Init Data ──

def init_db():
    db.create_all()

    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', name='系统管理员', role='admin', currency='MYR')
        admin.set_password('admin888')
        db.session.add(admin)

    if Tier.query.count() == 0:
        tiers_data = [
            {'name': 'Classic', 'min_downlines': 0, 'max_downlines': 4,
             'icon_file': 'classic.jpeg', 'sort_order': 0,
             'live_casino_rate': 0.30, 'slot_rate': 0.50,
             'birthday_bonus': 0, 'upgrade_bonus': 0, 'points_rate': 1.0},
            {'name': 'Silver', 'min_downlines': 5, 'max_downlines': 14,
             'icon_file': 'silver.jpeg', 'sort_order': 1,
             'live_casino_rate': 0.35, 'slot_rate': 0.55,
             'birthday_bonus': 68, 'upgrade_bonus': 68, 'points_rate': 2.0},
            {'name': 'Gold', 'min_downlines': 15, 'max_downlines': 29,
             'icon_file': 'gold.jpeg', 'sort_order': 2,
             'live_casino_rate': 0.40, 'slot_rate': 0.60,
             'birthday_bonus': 168, 'upgrade_bonus': 168, 'points_rate': 3.0},
            {'name': 'Emerald', 'min_downlines': 30, 'max_downlines': 59,
             'icon_file': 'emerald.jpeg', 'sort_order': 3,
             'live_casino_rate': 0.45, 'slot_rate': 0.70,
             'birthday_bonus': 388, 'upgrade_bonus': 388, 'points_rate': 5.0},
            {'name': 'Diamond', 'min_downlines': 60, 'max_downlines': 100,
             'icon_file': 'diamond.jpeg', 'sort_order': 4,
             'live_casino_rate': 0.50, 'slot_rate': 0.80,
             'birthday_bonus': 688, 'upgrade_bonus': 688, 'points_rate': 8.0},
        ]
        for td in tiers_data:
            db.session.add(Tier(**td))

    db.session.commit()


with app.app_context():
    init_db()


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
