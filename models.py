from datetime import datetime, date
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='agent')  # 'admin' or 'agent'
    name = db.Column(db.String(100), nullable=False)
    birthday = db.Column(db.Date, nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    bank_account = db.Column(db.String(100), nullable=True)
    bank_name = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    currency = db.Column(db.String(10), nullable=False, default='MYR')  # AUD, MYR, SGD
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    referrer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    referrer = db.relationship('User', remote_side=[id], backref=db.backref('downlines', lazy='dynamic'))

    tier_id = db.Column(db.Integer, db.ForeignKey('tiers.id'), nullable=True)
    tier = db.relationship('Tier', backref='users')

    total_deposit = db.Column(db.Float, default=0.0)
    is_verified = db.Column(db.Boolean, default=False)
    points = db.Column(db.Integer, default=0)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == 'admin'

    def get_all_downlines(self):
        """Recursively get all downlines (direct + indirect)."""
        result = []
        direct = User.query.filter_by(referrer_id=self.id, is_active=True).all()
        for d in direct:
            result.append(d)
            result.extend(d.get_all_downlines())
        return result

    def get_valid_downline_count(self):
        """Count downlines that meet the verification criteria."""
        all_down = self.get_all_downlines()
        return sum(1 for d in all_down if d.is_verified and d.total_deposit >= 100)

    def get_downline_tree(self):
        """Build a tree structure for visualization."""
        direct = User.query.filter_by(referrer_id=self.id, is_active=True).all()
        children = []
        for d in direct:
            children.append({
                'id': d.id,
                'name': d.name,
                'username': d.username,
                'tier': d.tier.name if d.tier else 'Classic',
                'tier_icon': d.tier.icon_file if d.tier else 'classic.jpeg',
                'verified': d.is_verified,
                'deposit': d.total_deposit,
                'children': d.get_downline_tree()
            })
        return children


class Tier(db.Model):
    __tablename__ = 'tiers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    min_downlines = db.Column(db.Integer, nullable=False, default=0)
    max_downlines = db.Column(db.Integer, nullable=False, default=5)
    icon_file = db.Column(db.String(100), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    live_casino_rate = db.Column(db.Float, nullable=False, default=0.0)
    slot_rate = db.Column(db.Float, nullable=False, default=0.0)

    birthday_bonus = db.Column(db.Float, nullable=False, default=0.0)
    upgrade_bonus = db.Column(db.Float, nullable=False, default=0.0)
    points_rate = db.Column(db.Float, nullable=False, default=0.0)  # commission to points %

    def __repr__(self):
        return f'<Tier {self.name}>'


class Transaction(db.Model):
    __tablename__ = 'transactions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    user = db.relationship('User', backref='transactions')
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), nullable=False, default='MYR')
    transaction_type = db.Column(db.String(30), nullable=False)  # deposit, commission, bonus
    category = db.Column(db.String(30), nullable=True)  # live_casino, slot, birthday, upgrade
    description = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Commission(db.Model):
    __tablename__ = 'commissions'

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    agent = db.relationship('User', foreign_keys=[agent_id], backref='commissions_earned')
    from_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    from_user = db.relationship('User', foreign_keys=[from_user_id])
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), nullable=False, default='MYR')
    category = db.Column(db.String(30), nullable=False)  # live_casino, slot
    turnover_amount = db.Column(db.Float, nullable=False, default=0.0)
    rate = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class SystemSetting(db.Model):
    __tablename__ = 'system_settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.String(500), nullable=False)
    description = db.Column(db.String(200), nullable=True)


class Gift(db.Model):
    __tablename__ = 'gifts'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    points_required = db.Column(db.Integer, nullable=False, default=0)
    stock = db.Column(db.Integer, nullable=False, default=0)
    image_url = db.Column(db.String(300), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PointTransaction(db.Model):
    __tablename__ = 'point_transactions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    user = db.relationship('User', backref='point_transactions')
    points = db.Column(db.Integer, nullable=False)
    balance_after = db.Column(db.Integer, nullable=False, default=0)
    transaction_type = db.Column(db.String(30), nullable=False)  # earn, redeem, admin_add, admin_deduct
    description = db.Column(db.String(200), nullable=True)
    gift_id = db.Column(db.Integer, db.ForeignKey('gifts.id'), nullable=True)
    gift = db.relationship('Gift', backref='redemptions')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
